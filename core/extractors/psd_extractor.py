"""
core/extractors/psd_extractor.py

Adobe Photoshop (.psd) extraction via psd-tools - a pure local parsing
library. No Photoshop installation required, no cloud APIs, nothing
uploaded anywhere.

Pulls together, as searchable text:
  - Document dimensions and color mode
  - Every layer and group name, at every nesting depth
    (PSDImage.descendants() walks the whole layer tree, groups
    included, regardless of how deeply nested)
  - Visible/hidden state per layer (marked inline, e.g. "Layer 3
    [hidden layer]", so it's both human-readable in a preview and
    findable via search if someone searches "hidden")
  - Editable text-layer contents - the actual rendered text
    (layer.text), which is not always the same string as the layer's
    display name

Local OCR fallback (controlled, not automatic on every file):
if the PSD's own editable text layers don't add up to much real text
(MIN_TEXT_CHARS_TO_SKIP_OCR), a flattened preview is composited
locally via psd_tools' own rendering (psd.composite(), returns a
Pillow Image in memory - no temp file, no re-opening in any external
tool) and OCR'd through the existing Tesseract pipeline
(core.ocr.extract_text_from_pil_image). A PSD with substantial live
type layers already has good searchable text, so OCR is skipped for
it entirely - this is what keeps OCR "controlled and incremental"
rather than run unconditionally on every PSD.

Known limitations (documented rather than silently pretended away):
  - Smart Object contents (embedded/linked files) are not recursed
    into - only the Smart Object layer's own name is captured.
  - Adjustment/effect layers contribute their name only, not a
    description of the adjustment itself.
  - Custom Photoshop metadata (XMP, image resources beyond what
    psd-tools' composite/layer APIs expose) is not parsed.
  - A PSD whose only content is rasterized art with generic layer
    names ("Layer 1", "Layer 2") and no live type layers relies
    entirely on the OCR fallback, same accuracy limits as any OCR.
"""

from pathlib import Path
from typing import Optional

try:
    from psd_tools import PSDImage
except ImportError:
    PSDImage = None

from core import ocr

# If real text-layer content already adds up to at least this many
# characters, skip the (much more expensive) OCR fallback entirely -
# there's already enough searchable text from live type layers.
MIN_TEXT_CHARS_TO_SKIP_OCR = 40


def is_available() -> bool:
    return PSDImage is not None


def extract(path: Path) -> Optional[str]:
    """
    Extract searchable text from a PSD: dimensions, color mode, every
    layer/group name at any nesting depth with visibility state, and
    editable text-layer contents - plus, only when that adds up to
    little real text, an OCR pass over a locally rendered flattened
    preview.

    Returns None if psd-tools isn't installed or the file can't be
    opened at all (corrupt file, unsupported/future PSD variant,
    etc.) - the file is still indexed by filename by the caller, same
    as any other extractor failure.
    """
    if PSDImage is None:
        return None

    try:
        psd = PSDImage.open(str(path))
    except Exception:
        return None

    parts: list[str] = []

    try:
        mode_name = psd.color_mode.name
    except Exception:
        mode_name = None
    dims = f"{psd.width}x{psd.height} pixels"
    parts.append(f"{dims} {mode_name}" if mode_name else dims)

    text_layer_chars = 0
    try:
        for layer in psd.descendants():
            name = (layer.name or "").strip()
            if name:
                parts.append(name if layer.is_visible() else f"{name} [hidden layer]")

            if layer.kind == "type":
                try:
                    layer_text = (layer.text or "").strip()
                except Exception:
                    layer_text = ""
                if layer_text:
                    parts.append(layer_text)
                    text_layer_chars += len(layer_text)
    except Exception:
        pass  # partial layer info is still better than none

    if text_layer_chars < MIN_TEXT_CHARS_TO_SKIP_OCR:
        ocr_text = _ocr_fallback(psd)
        if ocr_text:
            parts.append(ocr_text)

    text = "\n".join(parts)
    return text if text.strip() else None


def _ocr_fallback(psd) -> Optional[str]:
    """
    Render a flattened preview locally (psd_tools' own compositor -
    nothing external, nothing uploaded) and OCR it via the existing
    Tesseract pipeline. Never raises - a failure here just means no
    extra OCR text, same as any other OCR failure elsewhere in the app.
    """
    try:
        preview = psd.composite()
    except Exception:
        return None
    if preview is None:
        return None
    return ocr.extract_text_from_pil_image(preview)
