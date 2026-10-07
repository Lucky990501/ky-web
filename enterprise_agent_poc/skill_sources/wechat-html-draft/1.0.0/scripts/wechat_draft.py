"""HTML -> WeChat assets -> draft. No publish or mass-send endpoints.

Python 3.10+. Credentials stay in environment variables. --check is offline.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse

import css_inline
import requests
import tinycss2
from bs4 import BeautifulSoup
from PIL import Image
from workbench_guard import contained, fetch_image

API = "https://api.weixin.qq.com/cgi-bin/"
MAX_IMAGE = 10 * 1024 * 1024  # Local guard; WeChat may impose a lower endpoint limit.


class WorkflowError(Exception):
    pass


class APIError(WorkflowError):
    """An explicit WeChat error response; distinct from unknown request outcomes."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def whitelist_ips(message: str) -> list[str]:
    """Extract only validated IPs; never print credential-bearing error text."""
    addresses = []
    for value in re.findall(r"(?:invalid ip|ipv6)\s+([0-9a-fA-F:.]+)", message, re.I):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if str(address) not in addresses:
            addresses.append(str(address))
    return addresses


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {'account','title','digest','html','cover','author','content_source_url','body_selector','image_map'}
    if not isinstance(cfg, dict) or set(cfg) - allowed:
        raise WorkflowError('ARTICLE_CONFIG_FIELDS_BLOCKED')
    for key in ("account", "title", "digest", "html", "cover"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise WorkflowError(f"配置字段 {key} 必须为非空字符串。")
    for key in ("author", "content_source_url", "body_selector"):
        if key in cfg and not isinstance(cfg[key], str):
            raise WorkflowError(f"配置字段 {key} 必须为字符串。")
    if not isinstance(cfg.get("image_map", {}), dict):
        raise WorkflowError("image_map 必须是原图片地址到本地文件路径的映射。")
    if any(not isinstance(k, str) or not isinstance(v, str)
           for k, v in cfg.get("image_map", {}).items()):
        raise WorkflowError("image_map 的键和值必须为字符串。")
    source = cfg.get("content_source_url", "")
    if source and urlparse(source).scheme not in ("http", "https"):
        raise WorkflowError("原文地址必须是 http/https 地址。")
    return cfg


def workspace_root(config_path: Path) -> Path:
    root = Path(os.environ['WORKSPACE_ROOT']).resolve(strict=True) if os.environ.get('WORKSPACE_ROOT') else config_path.parent.resolve(strict=True)
    contained(root, config_path)
    return root


def image_data(data: bytes, cover: bool = False) -> tuple[bytes, str]:
    if not data or len(data) > MAX_IMAGE:
        raise WorkflowError("图片为空或超过本地 10 MiB 上限。")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            animated = getattr(img, "n_frames", 1) > 1
            img.verify()
        if animated:
            raise WorkflowError("动图需要单独适配；脚本不会静默转成静态图。")
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # Preserve supported originals, including alpha and image quality.
            if fmt in ("JPEG", "PNG"):
                return data, "image/jpeg" if fmt == "JPEG" else "image/png"
            if cover and fmt == "GIF":
                return data, "image/gif"
            out = io.BytesIO()
            img.convert("RGBA").save(out, format="PNG")
            converted = out.getvalue()
            if len(converted) > MAX_IMAGE:
                raise WorkflowError("转换后的 PNG 超过本地上限。")
            return converted, "image/png"
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("无法读取图片，请使用有效的 JPG 或 PNG 文件。") from exc


def prepare_html(cfg: dict, config_path: Path, allow_style_risk=False):
    root = workspace_root(config_path)
    html_path = contained(root, cfg["html"], base=config_path.parent)
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8-sig"), "html.parser")
    issues = []
    if soup.find(["script", "canvas", "iframe", "svg", "video", "audio", "object", "embed", "form", "picture", "base"]):
        raise WorkflowError("检测到脚本、SVG、媒体、picture 或交互内容；请先转成普通图片/静态 HTML。")
    for link in list(soup.find_all("link")):
        if "stylesheet" in link.get("rel", []):
            href = link.get("href", "")
            if urlparse(href).scheme or href.startswith("//"):
                raise WorkflowError("请先将远程 CSS 下载到本地，或写入 HTML 的 style 标签。")
            css_path = contained(root, href, base=html_path.parent)
            style = soup.new_tag("style")
            style.string = css_path.read_text(encoding="utf-8-sig")
            link.replace_with(style)
        else:
            link.decompose()
    css_sources = [x.get_text() for x in soup.find_all("style")]
    css_sources += [x.get("style", "") for x in soup.find_all(style=True)]
    css_text = "\n".join(css_sources)
    # Reject silently lost generated content, dependencies, or CSS-only pictures.
    if re.search(r"@import\b|url\s*\(|::?(?:before|after)\b", css_text, re.I):
        raise WorkflowError("存在 CSS 导入、背景图片/字体资源或伪元素，请改用真实文字及 img 图片。")
    if re.search(r"@(?:media|font-face|keyframes|supports)\b|var\s*\(|(?:flex|grid|gradient|transform|animation)\b", css_text, re.I):
        issues.append("检测到响应式、字体资源、CSS 变量或复杂布局，可能被微信过滤。")
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.lower().startswith("on"):
                del node[attr]
        if node.get("srcset") or node.get("data-src"):
            raise WorkflowError("检测到懒加载或 srcset，请先改为确定的 img src。")
        if node.name == "a":
            href = node.get("href", "")
            if href and urlparse(href).scheme not in ("http", "https", "mailto", "tel"):
                raise WorkflowError("链接包含相对路径、锚点或不支持的协议，请改成明确的完整地址。")
    inlined = css_inline.CSSInliner(load_remote_stylesheets=False).inline(str(soup))
    doc = BeautifulSoup(inlined, "html.parser")
    selector = cfg.get("body_selector", "")
    root = doc.select_one(selector) if selector else doc.body
    if root is None:
        raise WorkflowError("body_selector 未找到正文节点。")
    if selector and len(doc.select(selector)) != 1:
        raise WorkflowError("body_selector 必须只匹配一个正文节点。")
    # Preserve inherited body / ancestor styles after removing the document shell.
    ancestors = list(root.parents)
    fragment = str(root)
    for ancestor in ancestors:
        if ancestor.name == "[document]":
            break
        if ancestor.get("style"):
            wrapper = doc.new_tag("section")
            wrapper["style"] = ancestor["style"]
            wrapper.append(BeautifulSoup(fragment, "html.parser"))
            fragment = str(wrapper)
    content = BeautifulSoup(fragment, "html.parser")
    for tag in list(content.find_all(["style", "link", "meta", "title"])):
        tag.decompose()
    for tag in content.find_all(["html", "body", "main", "article"]):
        tag.name = "section"
    for tag in content.find_all(True):
        declarations = tinycss2.parse_declaration_list(tag.get("style", ""), skip_comments=True, skip_whitespace=True)
        for rule in declarations:
            if rule.type == "error":
                raise WorkflowError("存在无效的内联 CSS，请先修正文章样式。")
            if rule.type == "declaration" and rule.lower_name == "position":
                if tinycss2.serialize(rule.value).strip() != "static":
                    issues.append("定位布局可能被微信修改。")
        if tag.name == "img":
            if not tag.get("src"):
                raise WorkflowError("正文图片缺少 src。")
            # Preserve authored styling; only add responsive defaults when absent.
            names = {r.lower_name for r in declarations if r.type == "declaration"}
            additions = ""
            if "max-width" not in names:
                additions += "max-width:100%;"
            if "height" not in names and not tag.get("height"):
                additions += "height:auto;"
            tag["style"] = tag.get("style", "").rstrip(";") + ";" + additions
    issues = list(dict.fromkeys(issues))
    if issues and not allow_style_risk:
        raise WorkflowError(" ".join(issues) + " 修改后重试，或使用 --allow-style-risk 接受显示差异。")
    return content, html_path, issues


def local_image(src: str, base: Path, cfg: dict, config_path: Path):
    mapped = cfg.get("image_map", {}).get(src)
    if mapped:
        return image_data(contained(workspace_root(config_path), mapped, base=config_path.parent).read_bytes())
    if src.startswith("data:"):
        if not re.match(r"^data:image/(?:png|jpeg|jpg|gif|webp);base64,", src, re.I):
            raise WorkflowError("仅支持 base64 编码的常规图片。")
        try:
            raw = base64.b64decode(src.split(",", 1)[1], validate=True)
        except ValueError as exc:
            raise WorkflowError("图片的 base64 数据无效。") from exc
        return image_data(raw)
    parts = urlparse(src)
    if parts.scheme in ("http", "https") or src.startswith("//"):
        return None
    if parts.scheme:
        raise WorkflowError("图片协议不支持；请使用相对本地路径、HTTP(S) 或 base64 图片。")
    return image_data(contained(workspace_root(config_path), parts.path, base=base).read_bytes())


def download_image(url: str):
    return image_data(fetch_image(url, MAX_IMAGE))


class WeChat:
    def __init__(self):
        self.token = os.environ.get("WECHAT_ACCESS_TOKEN", "")
        self.appid = os.environ.get("WECHAT_APP_ID", "")
        secret = os.environ.get("WECHAT_APP_SECRET", "")
        if not self.appid:
            raise WorkflowError("请设置 WECHAT_APP_ID，用于绑定上传记录到正确账号。")
        if not self.token:
            if not secret:
                raise WorkflowError("请设置 WECHAT_APP_SECRET 或 WECHAT_ACCESS_TOKEN。")
            result = self.request("GET", "token", params={"grant_type": "client_credential", "appid": self.appid, "secret": secret}, authenticated=False)
            self.token = result.get("access_token", "")
            if not self.token:
                raise WorkflowError("获取 access_token 失败。")

    def request(self, method, endpoint, *, params=None, authenticated=True, payload=None, files=None):
        if (method, endpoint) not in {('GET','token'),('POST','material/add_material'),('POST','media/uploadimg'),('POST','draft/add'),('POST','draft/get')}:
            raise WorkflowError('WECHAT_ENDPOINT_BLOCKED')
        params = dict(params or {})
        if authenticated:
            params["access_token"] = self.token
        kwargs = {"params": params, "timeout": (10, 60), "allow_redirects": False}
        if payload is not None:
            kwargs["data"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            kwargs["headers"] = {"Content-Type": "application/json; charset=utf-8"}
        if files:
            kwargs["files"] = files
        try:
            response = requests.request(method, API + endpoint, **kwargs)
            if not 200 <= response.status_code < 300:
                raise WorkflowError(f"微信接口 {endpoint} HTTP {response.status_code}，请求结果待核实。")
            # Some WeChat responses use a text content type. Requests can then
            # default to Latin-1 even though the JSON bytes are UTF-8.
            response.encoding = "utf-8"
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Do not echo exception text: it can contain credential-bearing URLs.
            raise WorkflowError(f"微信接口 {endpoint} 网络异常或响应无效，请求结果待核实。") from exc
        if not isinstance(result, dict):
            raise WorkflowError(f"微信接口 {endpoint} 返回了异常格式。")
        if result.get("errcode", 0):
            code = result["errcode"]
            hints = {"40001": "检查凭据", "40014": "token 无效", "42001": "token 过期", "40164": "配置服务器 IP 白名单", "48001": "当前账号没有该接口权限", "45009": "接口调用超过限制"}
            hint = hints.get(str(code), '请根据微信官方错误码说明检查输入及接口权限。')
            if str(code) == "40164":
                addresses = whitelist_ips(str(result.get("errmsg", "")))
                if addresses:
                    hint += "。微信识别的请求出口 IP：" + ", ".join(addresses)
                    hint += "。请把以上 IP 加入该 AppID 对应公众号的 IP 白名单，保存后保持相同网络/代理设置重跑。"
                else:
                    hint += "。微信返回中未找到有效 IP，请在本机核对原始错误中的 invalid ip 字段。"
            raise APIError(f"微信接口 {endpoint} 错误码 {code}。{hint}")
        return result

    def upload(self, image, cover=False):
        data, mime = image
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}[mime]
        return self.request("POST", "material/add_material" if cover else "media/uploadimg", params={"type": "image"} if cover else {}, files={"media": (f"image.{extension}", data, mime)})


def normalized_text(html):
    return "".join(BeautifulSoup(html, "html.parser").get_text().split())


def check_body_size(html, estimate_image_urls=False):
    if estimate_image_urls:
        temporary = BeautifulSoup(html, "html.parser")
        for tag in temporary.find_all("img"):
            tag["src"] = "https://mmbiz.qpic.cn/" + "x" * 160
        html = str(temporary)
    if len(html) >= 20000 or len(html.encode("utf-8")) >= 1024 * 1024:
        raise WorkflowError("正文超过本地接口长度保护，请拆分文章。")


def wechat_image_identity(url):
    parts = urlparse(url or "")
    if parts.scheme not in ("http", "https") or parts.hostname not in ("mmbiz.qpic.cn", "mmbiz.qlogo.cn"):
        return None
    # WeChat may change HTTP to HTTPS and /0 to a display width such as /640.
    # Preserve the asset path, while ignoring delivery size and query options.
    return parts.hostname, re.sub(r"/\d+$", "", parts.path)


def verify_draft(api, state, article, run_dir):
    data = api.request("POST", "draft/get", payload={"media_id": state["draft_id"]})
    write_json(run_dir / "wechat-returned.json", data)
    items = data.get("news_item", [])
    if len(items) != 1:
        raise WorkflowError("草稿已创建，但回读文章数量不符，请到后台检查。")
    returned = items[0]
    mismatches = [key for key in ("title", "digest", "thumb_media_id") if returned.get(key) != article[key]]
    if normalized_text(returned.get("content", "")) != normalized_text(article["content"]):
        mismatches.append("正文文字")
    submitted_images = BeautifulSoup(article["content"], "html.parser").find_all("img")
    actual_images = BeautifulSoup(returned.get("content", ""), "html.parser").find_all("img")
    expected_images = [wechat_image_identity(x.get("src")) for x in submitted_images]
    returned_images = [wechat_image_identity(x.get("data-src") or x.get("src")) for x in actual_images]
    if len(submitted_images) != len(actual_images) or any(x is None for x in returned_images) or expected_images != returned_images:
        mismatches.append("正文图片数量或图片地址")
    report = {"draft_id": state["draft_id"], "field_mismatches": mismatches, "checked_image_count": len(actual_images), "visual_verified": False, "note": "接口回读不能证明像素一致，需在公众号后台及手机预览核对样式和封面裁切。"}
    write_json(run_dir / "verification.json", report)
    if mismatches:
        raise WorkflowError("草稿已创建，但回读存在差异：" + ", ".join(mismatches))
    print(f"草稿已创建并通过字段回读核对，media_id: {state['draft_id']}")
    print("请到公众号后台打开草稿，在手机预览中核对排版和封面裁切。")


def publish_to_draft(cfg, content, images, cover, run_dir, fingerprint, api, adopt_draft_id=None):
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"fingerprint": fingerprint, "appid": api.appid, "uploads": {}}
    if state.get("fingerprint") != fingerprint or state.get("appid") != api.appid:
        raise WorkflowError("上传记录属于不同的输入或 AppID；请为新文章/账号指定新的 --work-dir。")
    if adopt_draft_id:
        if state.get("pending") != "draft/add":
            raise WorkflowError("--adopt-draft-id 仅用于恢复结果不明确的 draft/add 请求。")
        article = json.loads((run_dir / "draft-payload.json").read_text(encoding="utf-8"))["articles"][0]
        candidate = dict(state, draft_id=adopt_draft_id)
        verify_draft(api, candidate, article, run_dir)
        candidate.pop("pending", None)
        write_json(state_path, candidate)
        print("已核对并接续现有草稿，未重新创建。")
        return
    if state.get("pending"):
        raise WorkflowError(f"上次 {state['pending']} 请求结果不明确。请先核对公众号后台；脚本不会自动重复提交。参见使用说明。")

    def mutate(key, action, field):
        if key in state["uploads"]:
            return state["uploads"][key]
        state["pending"] = key
        write_json(state_path, state)
        try:
            result = action()
        except APIError:
            state.pop("pending", None)
            write_json(state_path, state)
            raise
        value = result.get(field)
        if not value:
            raise WorkflowError("上传响应缺少必要字段，结果待核实。")
        state["uploads"][key] = value
        state.pop("pending", None)
        write_json(state_path, state)
        return value

    thumb = mutate("cover:" + digest(cover[0]), lambda: api.upload(cover, True), "media_id")
    for tag in content.find_all("img"):
        image = images[tag["src"]]
        key = "body:" + digest(image[0])
        tag["src"] = mutate(key, lambda image=image: api.upload(image), "url")
    html = str(content)
    check_body_size(html)
    article = {"title": cfg["title"], "digest": cfg["digest"], "author": cfg.get("author", ""), "content": html, "content_source_url": cfg.get("content_source_url", ""), "thumb_media_id": thumb, "need_open_comment": 0, "only_fans_can_comment": 0}
    write_json(run_dir / "draft-payload.json", {"articles": [article]})
    (run_dir / "uploaded.html").write_text(html, encoding="utf-8")
    if not state.get("draft_id"):
        state["pending"] = "draft/add"
        write_json(state_path, state)
        try:
            result = api.request("POST", "draft/add", payload={"articles": [article]})
        except APIError:
            state.pop("pending", None)
            write_json(state_path, state)
            raise
        if not result.get("media_id"):
            raise WorkflowError("创建草稿响应缺少 media_id，结果待核实。")
        state["draft_id"] = result["media_id"]
        state.pop("pending", None)
        write_json(state_path, state)
        print(f"草稿创建成功，media_id: {state['draft_id']}。正在回读核对。")
    verify_draft(api, state, article, run_dir)


def main():
    parser = argparse.ArgumentParser(description="HTML 文章及封面、标题、摘要上传到微信公众号草稿箱")
    parser.add_argument("config", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="离线预检，不获取 token 或上传")
    mode.add_argument("--run", action="store_true", help="上传图片并创建草稿")
    parser.add_argument("--work-dir", type=Path, help="保存预检、上传记录、回读报告；一份目录对应一篇文章及一个账号")
    parser.add_argument("--allow-style-risk", action="store_true", help="接受已识别的复杂 CSS 显示差异")
    parser.add_argument("--adopt-draft-id", help="回读核对并接续上次超时后已存在的草稿；不创建新草稿")
    args = parser.parse_args()
    if args.adopt_draft_id and not args.run:
        raise WorkflowError("--adopt-draft-id 需要与 --run 一起使用。")
    config_path = args.config.resolve()
    root = workspace_root(config_path)
    run_dir = contained(root, args.work_dir or config_path.parent / "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    for output in ('state.json','draft-payload.json','verification.json','wechat-returned.json','prepared.html','preflight.json','uploaded.html','.lock'):
        contained(root, run_dir / output)
        contained(root, (run_dir / output).with_suffix((run_dir / output).suffix + '.tmp'))
    lock_path = run_dir / ".lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise WorkflowError("已有进程使用该工作目录；确认无运行进程后才能删除 .lock。")
    os.close(lock_fd)
    try:
        cfg = read_config(config_path)
        content, html_path, issues = prepare_html(cfg, config_path, args.allow_style_risk)
        cover = image_data(contained(root, cfg["cover"], base=config_path.parent).read_bytes(), cover=True)
        images, remote = {}, []
        for tag in content.find_all("img"):
            src = tag["src"]
            if src in images:
                continue
            images[src] = local_image(src, html_path.parent, cfg, config_path)
            if images[src] is None:
                remote.append(src)
        html = str(content)
        if not content.get_text(strip=True) and not content.find("img"):
            raise WorkflowError("正文为空。")
        # Data URLs are replaced on upload; do not count them as final HTML text.
        check_body_size(html, estimate_image_urls=True)
        (run_dir / "prepared.html").write_text(html, encoding="utf-8")
        report = {"account": cfg["account"], "title": cfg["title"], "digest": cfg["digest"], "title_chars": len(cfg["title"]), "digest_chars": len(cfg["digest"]), "image_count": len(content.find_all("img")), "remote_images_not_checked_offline": len(remote), "style_warnings": issues, "visual_verified": False}
        write_json(run_dir / "preflight.json", report)
        print(f"本地预检通过：{cfg['account']}，正文 {len(html)} 字符，{len(images)} 张不同图片。")
        if remote:
            print(f"其中 {len(remote)} 张远程图片将在 --run 时下载并校验；离线预检尚未验证这些图片。")
        if args.check:
            print(f"预检文件：{run_dir}")
            return
        for src in remote:
            images[src] = download_image(src)
        fingerprint = digest(json.dumps({"config": cfg, "html": html, "images": {src: digest(image[0]) for src, image in images.items()}, "cover": digest(cover[0])}, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        print("图片已校验。开始上传素材并创建草稿。")
        publish_to_draft(cfg, content, images, cover, run_dir, fingerprint, WeChat(), args.adopt_draft_id)
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (WorkflowError, OSError, ValueError) as exc:
        print(f"处理停止：{exc}", file=sys.stderr)
        sys.exit(1)
