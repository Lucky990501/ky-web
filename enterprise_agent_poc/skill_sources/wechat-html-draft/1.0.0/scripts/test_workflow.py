"""Offline behavioral tests: no credentials and no live WeChat calls."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image
import wechat_draft as flow


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), "green").save(buffer, format="PNG")
    return buffer.getvalue(), "image/png"


class FakeAPI:
    appid = "test-account"

    def __init__(self, draft_failure=None, upload_failure=None):
        self.calls = []
        self.article = None
        self.draft_failure = draft_failure
        self.upload_failure = upload_failure
        self.readback_override = None

    def upload(self, image, cover=False):
        self.calls.append("cover" if cover else "body")
        if self.upload_failure:
            raise self.upload_failure
        return {"media_id": "cover-id"} if cover else {"url": "https://mmbiz.qpic.cn/test.png"}

    def request(self, method, endpoint, **kwargs):
        self.calls.append(endpoint)
        if endpoint == "draft/add":
            self.article = copy.deepcopy(kwargs["payload"]["articles"][0])
            if self.draft_failure:
                raise self.draft_failure
            return {"media_id": "draft-id"}
        if endpoint == "draft/get":
            return {"news_item": [self.readback_override or self.article]}
        raise AssertionError(endpoint)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = {"account": "测试号", "title": "中文标题 ✓", "digest": "中文摘要，保持完整。", "html": "body.html", "cover": "cover.png", "body_selector": "#article"}
        self.config = self.root / "article.json"
        self.config.write_text(json.dumps(self.cfg, ensure_ascii=False), encoding="utf-8")
        self.image = png()
        (self.root / "cover.png").write_bytes(self.image[0])
        (self.root / "body.html").write_text('<html><head><style>body{color:#333} p{font-size:17px}</style></head><body><nav>不上传导航</nav><section id="article"><p>中文正文。</p><img src="cover.png"><img src="cover.png"></section></body></html>', encoding="utf-8")
        self.content, _, _ = flow.prepare_html(self.cfg, self.config)
        self.run = self.root / "run"
        self.run.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, api, fingerprint="input-v1", adopt=None):
        return flow.publish_to_draft(self.cfg, copy.deepcopy(self.content), {"cover.png": self.image}, self.image, self.run, fingerprint, api, adopt)

    def state(self):
        return json.loads((self.run / "state.json").read_text(encoding="utf-8"))

    def test_css_inline_selected_body_and_inherited_style(self):
        self.assertNotIn("不上传导航", str(self.content))
        self.assertIn("font-size:17px", self.content.p["style"].replace(" ", ""))
        self.assertIn("color:#333", str(self.content).replace(" ", ""))
        self.assertIsNone(self.content.find("style"))

    def test_success_utf8_images_dedup_and_repeated_run(self):
        api = FakeAPI()
        self.upload(api)
        self.assertEqual(api.calls, ["cover", "body", "draft/add", "draft/get"])
        self.assertEqual(api.article["title"], self.cfg["title"])
        self.assertEqual(api.article["digest"], self.cfg["digest"])
        self.assertEqual(api.article["thumb_media_id"], "cover-id")
        self.assertEqual(len(BeautifulSoup(api.article["content"], "html.parser").find_all("img")), 2)
        self.assertNotIn('src="cover.png"', api.article["content"])
        self.upload(api)
        self.assertEqual(api.calls.count("draft/add"), 1)
        self.assertEqual(api.calls.count("body"), 1)
        report = json.loads((self.run / "verification.json").read_text(encoding="utf-8"))
        self.assertFalse(report["visual_verified"])

    def test_unknown_create_outcome_stops_retries_then_adopts(self):
        api = FakeAPI(draft_failure=flow.WorkflowError("timeout"))
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(self.state()["pending"], "draft/add")
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(api.calls.count("draft/add"), 1)
        self.upload(api, adopt="existing-id")
        self.assertNotIn("pending", self.state())
        self.assertEqual(self.state()["draft_id"], "existing-id")
        self.assertEqual(api.calls.count("draft/add"), 1)

    def test_wrong_recovery_candidate_keeps_pending(self):
        api = FakeAPI(draft_failure=flow.WorkflowError("timeout"))
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        api.readback_override = dict(api.article, title="另一篇文章")
        with self.assertRaises(flow.WorkflowError):
            self.upload(api, adopt="wrong-id")
        self.assertEqual(self.state()["pending"], "draft/add")
        self.assertNotIn("draft_id", self.state())

    def test_explicit_api_failure_can_retry(self):
        api = FakeAPI(draft_failure=flow.APIError("bad token"))
        with self.assertRaises(flow.APIError):
            self.upload(api)
        self.assertNotIn("pending", self.state())
        api.draft_failure = None
        self.upload(api)
        self.assertEqual(api.calls.count("cover"), 1)
        self.assertEqual(api.calls.count("body"), 1)
        self.assertEqual(api.calls.count("draft/add"), 2)

    def test_unknown_upload_outcome_is_not_repeated(self):
        api = FakeAPI(upload_failure=flow.WorkflowError("timeout"))
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(api.calls, ["cover"])

    def test_different_input_or_account_cannot_reuse_state(self):
        api = FakeAPI()
        self.upload(api)
        with self.assertRaises(flow.WorkflowError):
            self.upload(api, fingerprint="changed-input")
        api.appid = "another-account"
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(api.calls.count("draft/add"), 1)

    def test_readback_error_preserves_created_draft_id(self):
        api = FakeAPI()
        # Simulate WeChat stripping one paragraph on readback.
        api.readback_override = {"title": self.cfg["title"], "digest": self.cfg["digest"], "thumb_media_id": "cover-id", "content": "<p>丢失内容</p>"}
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(self.state()["draft_id"], "draft-id")
        with self.assertRaises(flow.WorkflowError):
            self.upload(api)
        self.assertEqual(api.calls.count("draft/add"), 1)

    def test_incompatible_html_is_blocked(self):
        cases = ["<svg><text>x</text></svg>", "<script>alert(1)</script>", '<p style="background:url(a.png)">x</p>', "<style>p::before{content:'x'}</style><p>x</p>"]
        for case in cases:
            with self.subTest(case=case):
                (self.root / "body.html").write_text(case, encoding="utf-8")
                with self.assertRaises(flow.WorkflowError):
                    flow.prepare_html(dict(self.cfg, body_selector=""), self.config)

    def test_complex_css_requires_explicit_flag(self):
        (self.root / "body.html").write_text('<section id="article" style="display:flex"><p>x</p></section>', encoding="utf-8")
        with self.assertRaises(flow.WorkflowError):
            flow.prepare_html(self.cfg, self.config)
        content, _, warnings = flow.prepare_html(self.cfg, self.config, True)
        self.assertTrue(warnings)
        self.assertIn("flex", str(content))

    def test_local_data_and_remote_image_routing(self):
        self.assertEqual(flow.local_image("cover.png", self.root, self.cfg, self.config), self.image)
        import base64
        src = "data:image/png;base64," + base64.b64encode(self.image[0]).decode("ascii")
        self.assertEqual(flow.local_image(src, self.root, self.cfg, self.config), self.image)
        self.assertIsNone(flow.local_image("https://example.com/p.png", self.root, self.cfg, self.config))
        mapped = dict(self.cfg, image_map={"https://example.com/p.png": "cover.png"})
        self.assertEqual(flow.local_image("https://example.com/p.png", self.root, mapped, self.config), self.image)
        with self.assertRaises(flow.WorkflowError):
            flow.image_data(b"not an image")

    def test_http_payload_unicode_and_token_redaction(self):
        api = object.__new__(flow.WeChat)
        api.token = "SECRET-TOKEN"
        response = unittest.mock.Mock(status_code=200)
        response.json.return_value = {"media_id": "draft-id"}
        with patch.object(flow.requests, "request", return_value=response) as mock:
            api.request("POST", "draft/add", payload={"title": "中文标题"})
            body = mock.call_args.kwargs["data"]
            self.assertEqual(json.loads(body.decode("utf-8")), {"title": "中文标题"})
            self.assertEqual(mock.call_args.kwargs["params"]["access_token"], "SECRET-TOKEN")
        with patch.object(flow.requests, "request", side_effect=flow.requests.ConnectionError("https://bad/?access_token=SECRET-TOKEN")):
            with self.assertRaises(flow.WorkflowError) as caught:
                api.request("POST", "draft/add", payload={})
            self.assertNotIn("SECRET-TOKEN", str(caught.exception))

    def test_size_guard_uses_uploaded_url_estimate_for_base64(self):
        html = '<p>短文</p><img src="data:image/png;base64,' + 'A' * 30000 + '">'
        flow.check_body_size(html, estimate_image_urls=True)
        with self.assertRaises(flow.WorkflowError):
            flow.check_body_size("<p>" + "文" * 20000 + "</p>")

    def test_whitelist_ip_error_identifies_egress_without_leaking_secrets(self):
        api = object.__new__(flow.WeChat)
        api.token = "SECRET-TOKEN"
        response = unittest.mock.Mock(status_code=200)
        response.json.return_value = {"errcode": 40164, "errmsg": "invalid ip 203.0.113.7 ipv6 ::ffff:203.0.113.7, not in whitelist access_token=SECRET-TOKEN secret=PRIVATE-SECRET"}
        with patch.object(flow.requests, "request", return_value=response):
            with self.assertRaises(flow.APIError) as caught:
                api.request("GET", "token")
            message = str(caught.exception)
            self.assertIn("203.0.113.7", message)
            self.assertEqual(message.count("203.0.113.7"), 1)
            self.assertNotIn("SECRET-TOKEN", message)
            self.assertNotIn("PRIVATE-SECRET", message)
        self.assertEqual(flow.whitelist_ips("invalid ip 999.999.999.999"), [])
        self.assertEqual(flow.whitelist_ips("invalid ip 2001:db8::2, not in whitelist"), ["2001:db8::2"])

    def test_utf8_json_with_wrong_response_encoding(self):
        api = object.__new__(flow.WeChat)
        api.token = "test-token"
        response = flow.requests.Response()
        response.status_code = 200
        response.encoding = "ISO-8859-1"
        response.headers["Content-Type"] = "text/plain"
        response._content = json.dumps({"news_item": [{"title": "给生活留一点空白", "digest": "完整的中文摘要。"}]}, ensure_ascii=False).encode("utf-8")
        with patch.object(flow.requests, "request", return_value=response):
            result = api.request("POST", "draft/get", payload={"media_id": "draft-id"})
        self.assertEqual(result["news_item"][0]["title"], "给生活留一点空白")
        self.assertEqual(result["news_item"][0]["digest"], "完整的中文摘要。")

    def test_wechat_data_src_and_image_size_rewrite_are_accepted(self):
        api = FakeAPI()
        self.upload(api)
        submitted = api.article["content"].replace("https://mmbiz.qpic.cn/test.png", "http://mmbiz.qpic.cn/mmbiz_png/asset-id/0?from=appmsg")
        returned = submitted.replace('src="http://mmbiz.qpic.cn/mmbiz_png/asset-id/0?from=appmsg"', 'data-src="https://mmbiz.qpic.cn/mmbiz_png/asset-id/640?from=appmsg"')
        article = dict(api.article, content=submitted)
        api.readback_override = dict(article, content=returned)
        flow.verify_draft(api, self.state(), article, self.run)
        report = json.loads((self.run / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(report["field_mismatches"], [])
        self.assertEqual(report["checked_image_count"], 2)

    def test_same_image_count_with_wrong_asset_is_rejected(self):
        api = FakeAPI()
        self.upload(api)
        api.readback_override = dict(api.article, content=api.article["content"].replace("test.png", "wrong.png"))
        with self.assertRaises(flow.WorkflowError):
            flow.verify_draft(api, self.state(), api.article, self.run)


if __name__ == "__main__":
    unittest.main()
