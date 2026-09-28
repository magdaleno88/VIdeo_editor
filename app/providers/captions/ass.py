import re
import textwrap
from pathlib import Path

from app.schemas.domain import CaptionPosition, CaptionStyleProfile, EmphasisMode


def escape_ass_text(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r", " ")
        .replace("\n", r"\N")
    )


def escape_srt_text(value: str) -> str:
    return " ".join(value.replace("-->", "—").splitlines()).strip()


def ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}"


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{fraction:03d}"


STYLE_VALUES = {
    CaptionStyleProfile.CLEAN: (58, 700, 4, 2, "&H7A000000", "&H00FFFFFF"),
    CaptionStyleProfile.BOLD: (64, 700, 5, 2, "&H96000000", "&H00FFFFFF"),
    CaptionStyleProfile.MINIMAL: (52, 400, 3, 1, "&H00000000", "&H00FFFFFF"),
}


class ASSSubtitleRenderer:
    version = "ass-1.0"

    @staticmethod
    def _styled_text(item, max_characters: int, spans: list[dict] | None = None) -> str:
        max_lines = max(1, int(item.style.get("max_lines", 2)))
        wrapped = r"\N".join(
            escape_ass_text(line)
            for line in textwrap.wrap(
                item.display_text,
                width=max(10, max_characters // max_lines),
                break_long_words=False,
            )
        )
        for span in spans if spans is not None else item.emphasis_spans:
            term = escape_ass_text(str(span.get("text", "")))
            if term and term in wrapped:
                wrapped = wrapped.replace(term, rf"{{\c&H00D7FF&\b1}}{term}{{\c&H00FFFFFF&\b0}}", 1)
        return wrapped

    def render(self, plan, width: int, height: int, font_path: Path | None = None) -> str:
        size, weight, outline, shadow, back, primary = STYLE_VALUES[plan.style_profile]
        size = round(size * height / 1920)
        margins = plan.safe_area
        margin_l = round(width * margins["left"])
        margin_r = round(width * margins["right"])
        margin_v = round(height * margins["bottom"])
        font_name = font_path.stem if font_path else "Arial"
        header = (
            "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {width}\nPlayResY: {height}\n\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,"
            "Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Caption,{font_name},{size},{primary},&H0000D7FF,&H00000000,{back},"
            f"{-1 if weight >= 700 else 0},0,0,0,100,100,0,0,1,{outline},{shadow},2,"
            f"{margin_l},{margin_r},{margin_v},1\n"
            f"Style: Overlay,{font_name},{round(size * 1.15)},{primary},&H0000D7FF,"
            f"&H00000000,{back},-1,0,0,0,100,100,0,0,1,{outline},{shadow},8,"
            f"{margin_l},{margin_r},{round(height * margins['top'])},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )
        events: list[str] = []
        alignments = {
            CaptionPosition.UPPER: 8,
            CaptionPosition.CENTER: 5,
            CaptionPosition.LOWER: 2,
        }
        for item in plan.items:
            static_spans = [span for span in item.emphasis_spans if "start_seconds" not in span]
            text = self._styled_text(item, int(item.style.get("max_characters", 42)), static_spans)
            events.append(
                f"Dialogue: 0,{ass_time(item.start_seconds)},{ass_time(item.end_seconds)},"
                f"Caption,,0,0,0,,{{\\an{alignments[item.position_name]}}}{text}"
            )
            for span in item.emphasis_spans:
                if "start_seconds" not in span or "end_seconds" not in span:
                    continue
                highlighted = self._styled_text(
                    item, int(item.style.get("max_characters", 42)), [span]
                )
                events.append(
                    f"Dialogue: 1,{ass_time(float(span['start_seconds']))},"
                    f"{ass_time(float(span['end_seconds']))},Caption,,0,0,0,,"
                    f"{{\\an{alignments[item.position_name]}}}{highlighted}"
                )
        for overlay in plan.overlays:
            text = escape_ass_text(overlay.text)
            events.append(
                f"Dialogue: {overlay.z_index},{ass_time(overlay.start_seconds)},"
                f"{ass_time(overlay.end_seconds)},Overlay,,0,0,0,,"
                f"{{\\an{alignments[overlay.position_name]}}}{text}"
            )
        return header + "\n".join(events) + "\n"


class SRTSubtitleRenderer:
    version = "srt-1.0"

    def render(self, plan) -> str:
        blocks = []
        for index, item in enumerate(plan.items, 1):
            blocks.append(
                f"{index}\n{srt_time(item.start_seconds)} --> {srt_time(item.end_seconds)}\n"
                f"{escape_srt_text(item.display_text)}"
            )
        return "\n\n".join(blocks) + "\n"


def emphasis_spans(text: str, limit: int, mode: EmphasisMode) -> list[dict[str, str | int]]:
    if mode == EmphasisMode.NONE or limit <= 0:
        return []
    candidates = re.findall(r"\b[\wÀ-ÿ°]+(?:\s+°?[A-Za-z]+)?\b", text, flags=re.UNICODE)
    ranked = sorted(
        (value.strip() for value in candidates if len(value.strip()) >= 5),
        key=lambda value: (-len(value), text.index(value)),
    )
    return [{"text": value, "kind": mode.value} for value in ranked[:limit]]
