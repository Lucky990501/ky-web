"""Bounded image intent planning. Pixel forwarding is not Provider certification."""
from __future__ import annotations

from fractions import Fraction
import re
import unicodedata

PROMPT_VERSION = "image-request-v1"
RATIO_SIZES = {"1:1": "1024x1024", "9:16": "864x1536", "16:9": "1536x864",
               "4:5": "1024x1280", "3:2": "1536x1024", "2:3": "1024x1536"}
MAX_OUTPUTS = 5
INPUT_ERRORS = {
    "image_size_invalid": "图片尺寸格式无效，请提供明确像素。",
    "image_size_not_verified": "该图片尺寸尚未完成当前模型能力核验，未发送生成请求；请确认使用明确像素候选。",
    "image_size_conflict": "工具尺寸与用户指定像素不一致，未发送生成请求。",
    "image_aspect_ratio_conflict": "图片像素与用户要求比例冲突，未发送生成请求。",
    "image_aspect_ratio_invalid": "图片比例无效，请确认比例或明确像素。",
    "image_output_plan_conflict": "图片用途、Prompt或数量不完整，未发送生成请求。",
    "image_count_limit": "单任务支持1～5张图片，请拆分需求。",
    "reference_batch_contract_required": "参考图编辑当前每任务支持一张结果，请拆分任务；未发送额外生成请求。",
    **{code: "当前企业原始Logo缺失或不可用，请先提供有效透明PNG；未调用图片服务。"
       for code in ("LOGO_FILE_REQUIRED", "LOGO_PERMISSION_DENIED", "LOGO_STORAGE_FAILED", "LOGO_FORMAT_NOT_SUPPORTED",
                    "LOGO_INVALID_PNG", "LOGO_TRANSPARENCY_REQUIRED", "LOGO_TOO_LARGE", "LOGO_DIMENSIONS_TOO_LARGE")},
}


class ImageRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("×", "x").replace("：", ":")


def requested_count(text: str) -> int:
    text = normalized(text)
    counts = []
    for match in re.finditer(r"([1-9]\d*|一|二|两|三|四|五|六|七|八|九|十)\s*(?:张|幅)(图片|图|海报|宣传图)?", text):
        before, after = text[max(0,match.start()-12):match.start()], text[match.end():match.end()+10]
        if re.search(r"(?:参考图|原图|素材)[：:]?\s*$", before) or re.match(r"\s*(?:参考图|参考图片|原图|素材|卡片|券)", after):
            continue
        if not match.group(2) and after.strip(" ，,。；;！!") and not re.search(r"生成|制作|输出|设计|做|来|要|正反|正面|背面|反面", before):
            continue
        value = match.group(1)
        counts.append(int(value) if value.isdigit() else {"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}[value])
    if counts:
        if len(set(counts)) > 1:
            raise ImageRequestError("image_output_plan_conflict", "本轮存在不同图片张数要求，请确认总张数及每张用途。")
        count = 2 if len(counts) == 2 and counts == [1,1] and re.search(r"正面.*(?:背面|反面)", text) else counts[0]
    else:
        count = 2 if re.search(r"正反(?:两)?面|正面.{0,8}(?:和|与|及|、).{0,4}(?:反面|背面)", text) else 1
    if not 1 <= count <= MAX_OUTPUTS:
        raise ImageRequestError("image_count_limit", "每个任务支持1～5张图片，请拆分需求。")
    return count


def pixel_size(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*(\d{3,4})\s*[xX]\s*(\d{3,4})\s*", normalized(value))
    if not match:
        raise ImageRequestError("image_size_invalid", "尺寸请使用明确像素，例如1024x1024。")
    width, height = map(int, match.groups())
    if not (256 <= width <= 1536 and 256 <= height <= 1536 and width % 8 == height % 8 == 0):
        raise ImageRequestError("image_size_not_verified", "该像素尺寸尚未完成当前图片模型能力核验，未发送生成请求；当前候选上限为1536像素且边长为8的倍数。")
    ratio = Fraction(width, height)
    return f"{width}x{height}", f"{ratio.numerator}:{ratio.denominator}"


def resolve_size(user_text: str, prompt: str, aspect_ratio: str, size: str | None) -> tuple[str, str]:
    """Trusted current user input wins; never silently replace explicit pixels."""
    source = normalized(user_text or prompt)
    # Source-image geometry describes input, not the desired output canvas.
    source = re.sub(r"(?:参考图|原图|源图|上传图片)(?:本身)?(?:的)?(?:尺寸|比例|分辨率)?(?:为|是|[:：])\s*(?:\d{3,4}\s*[xX]\s*\d{3,4}|\d{1,2}:\d{1,2}|[24][kK])", "参考图", source)
    resolution_text = re.sub(r'[“「『][^”」』\n]*[”」』]|"[^"\n]*"', '', source)
    resolution_text = re.sub(r"[24]k\s*(?:显示器|屏幕|电视|摄像头|投影仪|设备|型号)", "设备", resolution_text, flags=re.I)
    if re.search(r"(?:生成|制作|输出|分辨率|尺寸|要|改成|改为|调整为).{0,12}(?:2k|4k)|(?:2k|4k).{0,8}(?:图片|海报|分辨率|画质|尺寸)|^\s*[24]k\s*$", resolution_text, re.I) and not re.search(r"(?:不要|无需|不需要)\s*[24]k", resolution_text, re.I):
        raise ImageRequestError("image_size_not_verified", "当前模型的显式2K/4K能力尚未核验，不能保证；请确认改用明确像素候选后再生成。")
    explicit = re.search(r"(?<!\d)(\d{3,4})\s*[xX]\s*(\d{3,4})(?!\d)", source)
    ratio_match = re.search(r"(?<!\d)(1:1|9:16|16:9|4:5|3:2|2:3)(?!\d)", source)
    desired_ratio = ratio_match.group(1) if ratio_match else None
    if explicit:
        requested, actual_ratio = pixel_size("x".join(explicit.groups()))
        if size and pixel_size(size)[0] != requested:
            raise ImageRequestError("image_size_conflict", "Tool尺寸与用户明确指定的像素不一致，未发送生成请求。")
        if desired_ratio and actual_ratio != desired_ratio:
            raise ImageRequestError("image_aspect_ratio_conflict", "用户指定的像素和比例互相冲突，请确认优先要求。")
        return requested, actual_ratio
    if not desired_ratio:
        for pattern, value in ((r"长图", "9:16"), (r"竖版|竖向", "4:5"),
                               (r"横幅|横版|横向", "16:9"), (r"方图|正方形", "1:1"), (r"三折页", "3:2")):
            if re.search(pattern, source):
                desired_ratio = value
                break
    if size:
        requested, actual_ratio = pixel_size(size)
        if desired_ratio and actual_ratio != desired_ratio:
            raise ImageRequestError("image_aspect_ratio_conflict", "Tool尺寸不符合用户要求的比例，未发送生成请求。")
        return requested, actual_ratio
    ratio = desired_ratio or aspect_ratio
    if ratio not in RATIO_SIZES:
        raise ImageRequestError("image_aspect_ratio_invalid", "请选择1:1、9:16、16:9、4:5、3:2或2:3，或给出明确像素。")
    return RATIO_SIZES[ratio], ratio


def image_plan(user_text: str, prompt: str, aspect_ratio: str, size: str | None,
               outputs: list[dict] | None = None) -> list[dict]:
    count = requested_count(user_text or prompt)
    resolved_size, ratio = resolve_size(user_text, prompt, aspect_ratio, size)
    if outputs is not None and (len(outputs) != count or not all(isinstance(item, dict) and set(item) == {"purpose", "prompt"}
            and all(isinstance(item[key], str) and item[key].strip() for key in item) for item in outputs)):
        raise ImageRequestError("image_output_plan_conflict", "每张图片的用途和Prompt必须完整，数量须与用户要求一致。")
    purposes = ["正面", "背面"] if count == 2 and re.search(r"正反|正面|背面|反面", user_text or prompt) else [f"图片{i+1}" for i in range(count)]
    return [{"index": i, "purpose": item["purpose"], "prompt": item["prompt"],
             "size": resolved_size, "aspect_ratio": ratio, "prompt_version": PROMPT_VERSION}
            for i, item in enumerate(outputs or [{"purpose": purpose, "prompt": prompt if count == 1 else
                f"{prompt}\n本次只制作{purpose}，输出一张独立图片；其他面的内容由独立请求生成，不要把两面拼在一张图上。"}
                for purpose in purposes])]
