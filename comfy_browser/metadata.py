"""
Extracts generation metadata (checkpoint, LoRAs, embeddings, seed, sampler,
prompts) from ComfyUI's PNG text chunks.

ComfyUI embeds the full node graph as JSON in a PNG text chunk called
"prompt". Each node has a class_type and inputs dict. We don't know every
possible node type, so instead of one big if/elif chain, each node type
we care about gets its own small handler function registered below.
Adding support for a new node type means adding a function + one line in
the registry, not editing existing logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

EMPTY_FIELDS = {
    "checkpoints": [], "loras": [], "embeddings": [],
    "seeds": [], "samplers": [], "positive_prompt": "",
    "negative_prompt": "", "width": None, "height": None,
}


@dataclass
class ExtractedInfo:
    """Mutable accumulator used while walking the node graph."""
    checkpoints: set = field(default_factory=set)
    loras: set = field(default_factory=set)
    embeddings: set = field(default_factory=set)
    seeds: set = field(default_factory=set)
    samplers: set = field(default_factory=set)
    positive_prompt: str = ""
    negative_prompt: str = ""
    width: Optional[int] = None
    height: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "checkpoints": sorted(self.checkpoints),
            "loras": sorted(self.loras),
            "embeddings": sorted(self.embeddings),
            "seeds": sorted(self.seeds),
            "samplers": sorted(self.samplers),
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
        }


# ---------- Per-node-type handlers ----------
# Each handler receives (inputs_dict, info) and mutates info in place.
# Registered against the class_type strings that should trigger them.

NodeHandler = Callable[[dict, ExtractedInfo], None]
_HANDLERS: dict[str, NodeHandler] = {}


def register(*class_types: str):
    """Decorator to register a handler for one or more ComfyUI class_types."""
    def decorator(fn: NodeHandler) -> NodeHandler:
        for ct in class_types:
            _HANDLERS[ct] = fn
        return fn
    return decorator


@register("CheckpointLoader", "CheckpointLoaderSimple")
def _handle_checkpoint(inputs: dict, info: ExtractedInfo) -> None:
    ckpt = inputs.get("ckpt_name")
    if ckpt:
        info.checkpoints.add(ckpt)


@register("LoraLoader", "LoraLoaderModelOnly")
def _handle_lora(inputs: dict, info: ExtractedInfo) -> None:
    lora = inputs.get("lora_name")
    if not lora:
        return
    strength = inputs.get("strength_model", inputs.get("strength", ""))
    info.loras.add(f"{lora} ({strength})" if strength != "" else lora)


@register("KSampler", "KSamplerAdvanced")
def _handle_sampler(inputs: dict, info: ExtractedInfo) -> None:
    seed = inputs.get("seed", inputs.get("noise_seed"))
    if seed is not None:
        info.seeds.add(seed)
    sampler_name = inputs.get("sampler_name")
    if sampler_name:
        info.samplers.add(sampler_name)


@register("EmptyLatentImage")
def _handle_latent_size(inputs: dict, info: ExtractedInfo) -> None:
    info.width = inputs.get("width")
    info.height = inputs.get("height")


_EMBEDDING_PATTERN = re.compile(r"embedding:([^\s,>():]+)")


@register("CLIPTextEncode")
def _handle_text_encode(inputs: dict, info: ExtractedInfo) -> None:
    text = inputs.get("text", "")
    if not isinstance(text, str):
        return
    for emb in _EMBEDDING_PATTERN.findall(text):
        info.embeddings.add(emb)


def _matches_lora_fallback(class_type: str) -> bool:
    """Catch custom/third-party LoRA loader variants not explicitly named."""
    return class_type.startswith("Lora") and class_type not in _HANDLERS


# Node types known to pass a single CONDITIONING input through to their
# output, possibly modified, without combining multiple distinct prompts.
# When tracing back from a sampler's positive/negative input, we follow
# through these rather than stopping at them — they're not the source.
_CONDITIONING_PASSTHROUGH_TYPES = {
    "ConditioningSetArea",
    "ConditioningSetMask",
    "ControlNetApply",
    "ControlNetApplyAdvanced",
    "ConditioningZeroOut",
    "GLIGENTextBoxApply",
}

# A connection in ComfyUI's prompt JSON is represented as [node_id, output_index].
_MAX_TRACE_HOPS = 10  # guard against any unexpected cycles in malformed graphs


def _resolve_prompt_text(prompt_graph: dict, link: object, hops: int = 0) -> Optional[str]:
    """Follow a [node_id, output_index] connection backward through the
    graph until it reaches a CLIPTextEncode node, returning its text.
    Passes through known pass-through conditioning nodes; stops (returns
    None) at anything else it doesn't recognise, rather than guessing."""
    if hops > _MAX_TRACE_HOPS:
        return None
    if not (isinstance(link, list) and len(link) == 2):
        return None

    source_node_id = str(link[0])
    source_node = prompt_graph.get(source_node_id)
    if not isinstance(source_node, dict):
        return None

    class_type = source_node.get("class_type", "")
    inputs = source_node.get("inputs", {})

    if class_type == "CLIPTextEncode":
        text = inputs.get("text", "")
        return text if isinstance(text, str) else None

    if class_type in _CONDITIONING_PASSTHROUGH_TYPES:
        # Follow the first conditioning-shaped input we find onward.
        upstream = inputs.get("conditioning")
        if upstream is None:
            # Some nodes don't name it "conditioning" consistently;
            # fall back to scanning for any [id, idx]-shaped input.
            for v in inputs.values():
                if isinstance(v, list) and len(v) == 2:
                    upstream = v
                    break
        return _resolve_prompt_text(prompt_graph, upstream, hops + 1)

    return None


def _resolve_positive_negative(prompt_graph: dict, info: ExtractedInfo) -> None:
    """Find KSampler-family nodes and trace their positive/negative
    inputs back to the actual CLIPTextEncode node feeding them, rather
    than guessing based on visit order."""
    for node in prompt_graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in ("KSampler", "KSamplerAdvanced"):
            continue

        inputs = node.get("inputs", {})

        if not info.positive_prompt:
            pos_text = _resolve_prompt_text(prompt_graph, inputs.get("positive"))
            if pos_text:
                info.positive_prompt = pos_text

        if not info.negative_prompt:
            neg_text = _resolve_prompt_text(prompt_graph, inputs.get("negative"))
            if neg_text:
                info.negative_prompt = neg_text


def extract_from_prompt_graph(prompt: dict) -> dict:
    """Walk a parsed ComfyUI prompt graph and return extracted metadata as a dict."""
    info = ExtractedInfo()

    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})

        handler = _HANDLERS.get(class_type)
        if handler is None and _matches_lora_fallback(class_type):
            handler = _handle_lora
        if handler:
            handler(inputs, info)

    _resolve_positive_negative(prompt, info)

    return info.to_dict()


def extract_png_metadata(path: Path) -> Optional[dict]:
    """Read a PNG file and return extracted metadata, or None if it has
    no ComfyUI 'prompt' chunk or it isn't valid JSON.

    Uses img.info rather than img.text: PIL's .text property is a
    @property that calls self.load() to guarantee every tEXt chunk has
    been parsed, which decodes the full pixel data as a side effect —
    extremely expensive for thousands of files when we only want a
    small text chunk. img.info is already populated by Image.open()
    for the chunks PIL encounters before the image data starts (which
    is where ComfyUI puts its metadata), with no decode needed.
    """
    try:
        img = Image.open(path)
        prompt_raw = img.info.get("prompt")
    except Exception:
        return None

    if not prompt_raw:
        return None

    try:
        prompt_graph = json.loads(prompt_raw)
    except Exception:
        return None

    return extract_from_prompt_graph(prompt_graph)
