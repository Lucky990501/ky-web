"""Production d906 regressions. All model/Provider responses here are stubs.

These tests prove contracts, not actual N1N capabilities or model adherence.
"""
import asyncio
import base64
import hashlib
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.activity_brief import assess_activity, illustration_briefs, with_user_context
from app.agent_catalog import CATALOG
from app.brand_logo import BrandLogoError, BrandLogoService
from app.bundled_skills import validate_bundle
from app.image_branding import overlay_brand_logo
from app.image_request_contract import ImageRequestError, image_plan, resolve_size
from app.platform_mcp.service import PlatformMCPService
from app.product_store import ProductStore
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.settings import Settings
from app.storage import storage_provider
from app.wechat_article_format import render_wechat_article
from test_activity_plan_runtime import ScriptedProvider, collect, plan, ORDINARY_FULL_REQUESTS
from test_image_generation_provider import image_service
from test_chat_reference_image import chat_image_env, picture
from test_run_trace import product_task_fixture
from test_task_finalization import CallsRuntime, call


def png(size=(1024, 1024), color=(30, 90, 130, 255)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return output.getvalue()


def stub_provider(monkeypatch, sizes, statuses=None):
    observed = []
    statuses = statuses or [200] * len(sizes)

    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, **kwargs):
            index = len(observed)
            observed.append({"url": url, **{k:v for k,v in kwargs.items() if k != "headers"}})
            return httpx.Response(statuses[index], json={"data": [{"b64_json": base64.b64encode(png(sizes[index])).decode()}]})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return observed


@pytest.mark.parametrize("text,size,ratio", [
    ("生成一张图", "1024x1024", "1:1"),
    ("生成1:1方图", "1024x1024", "1:1"),
    ("生成9:16图片", "864x1536", "9:16"),
    ("生成16:9图片", "1536x864", "16:9"),
    ("做竖版海报", "1024x1280", "4:5"),
    ("做长图", "864x1536", "9:16"),
    ("做横幅", "1536x864", "16:9"),
    ("做三折页", "1536x1024", "3:2"),
    ("竖版海报明确16:9", "1536x864", "16:9"),
    ("生成1536×1024图片", "1536x1024", "3:2"),
])
def test_original_intent_overrides_omitted_or_default_tool_ratio(text, size, ratio):
    result = image_plan(text, "Tool遗漏用户尺寸", "1:1", None)
    assert result[0]["size"] == size and result[0]["aspect_ratio"] == ratio
    width, height = map(int, size.split("x"))
    a, b = map(int, ratio.split(":"))
    assert width * b == height * a


@pytest.mark.parametrize("text,size,code", [
    ("生成一张2K图片", None, "image_size_not_verified"),
    ("生成一张4K分辨率图片", None, "image_size_not_verified"),
    ("2K", None, "image_size_not_verified"),
    ("改成4K", None, "image_size_not_verified"),
    ("生成1536x1024", "1024x1024", "image_size_conflict"),
    ("生成9:16", "1024x1536", "image_aspect_ratio_conflict"),
    ("生成图", "2048x2048", "image_size_not_verified"),
    ("生成图", "NaNx1024", "image_size_invalid"),
    ("生成1536x1024、1:1", None, "image_aspect_ratio_conflict"),
])
def test_unverified_or_conflicting_size_rejected(text, size, code):
    with pytest.raises(ImageRequestError) as error:
        image_plan(text, "prompt", "1:1", size)
    assert error.value.code == code


@pytest.mark.parametrize("text", ["做三折页正反面两张", "做三折页正反面", "正面和背面分别生成"])
def test_front_back_are_separate_prompts(text):
    result = image_plan(text, "为教育企业设计三折页", "1:1", None)
    assert len(result) == 2
    assert [x["purpose"] for x in result] == ["正面", "背面"]
    assert result[0]["prompt"] != result[1]["prompt"]
    assert all("不要把两面拼在一张图" in x["prompt"] for x in result)


def test_output_plan_must_match_requested_count():
    with pytest.raises(ImageRequestError, match="数量"):
        image_plan("做两张图片", "图", "1:1", None, [{"purpose":"正面", "prompt":"one"}])
    with pytest.raises(ImageRequestError, match="1～5"):
        image_plan("做六张图片", "图", "1:1", None)
    planned = image_plan("做两张图", "图", "1:1", None,
                         [{"purpose":"正面", "prompt":"one"}, {"purpose":"背面", "prompt":"two"}])
    assert [x["prompt"] for x in planned] == ["one", "two"]


@pytest.mark.parametrize("message,count", [
    ("参考图1张，生成两张海报",2), ("使用一张参考图，制作三折页正反面两张",2),
    ("生成一张海报，文案写每人赠送两张卡片",1), ("三折页正面一张、背面一张",2),
])
def test_input_image_and_copy_counts_are_not_output_counts(message,count):
    assert len(image_plan(message,"图","1:1",None)) == count


@pytest.mark.parametrize("message", ["参考图尺寸是1024x1024，请编辑成16:9", "原图是1024x1024，请改成16:9"])
def test_source_geometry_does_not_override_requested_output(message):
    assert image_plan(message,"图","1:1",None)[0]["size"] == "1536x864"


def test_two_independent_requests_and_all_objects_persisted(tmp_path, monkeypatch):
    service, settings, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [(1536,1024)] * 2)
    result = asyncio.run(service.image_generation(token, "三折页正反面两张", [], "1:1"))
    assert len(observed) == len(result["results"]) == 2
    assert all(x["json"]["n"] == 1 and x["json"]["size"] == "1536x1024" for x in observed)
    assert observed[0]["json"]["prompt"] != observed[1]["json"]["prompt"]
    assert result["delivery"] == {"requested":2,"generated":2,"failed":0,"status":"completed","provider_attempts":2}
    for image in result["results"]:
        assert image["dimension_match"] is True
        assert storage_provider(settings).get(image["storage_key"])


def test_partial_generation_is_honest_and_no_paid_retry(tmp_path, monkeypatch):
    service, _, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [(1024,1024)] * 2, [200,500])
    result = asyncio.run(service.image_generation(token, "两张图片", [], "1:1"))
    assert len(observed) == 2
    assert result["delivery"]["status"] == "partial"
    assert result["delivery"]["generated"] == result["delivery"]["failed"] == 1
    assert result["failures"][0]["provider_status"] == 500
    trace = __import__("app.service", fromlist=["AgentService"]).AgentService._completed_trace(
        {"artifacts":{"enterprise_context_used":None,"knowledge_context_used":[],"assets_used":[]}}, __import__("app.domain", fromlist=["RuntimeTurn"]).RuntimeTurn("t", "两张都成功",
            mcp_calls=({**call("completed", tool="image_generation"), "image_delivery": result["delivery"]},)), True)
    assert "部分" in trace["final_result"] and "两张都成功" not in trace["final_result"]


def test_quota_failure_stops_unattempted_batch_members(tmp_path, monkeypatch):
    service, _, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [(1024,1024)] * 2, [200,429])
    result = asyncio.run(service.image_generation(token, "三张图片", [], "1:1"))
    assert len(observed) == 2 and result["delivery"]["provider_attempts"] == 2
    assert result["failures"][1]["code"] == "not_attempted_after_provider_rejection"


def test_provider_dimensions_never_upscaled_or_misreported(tmp_path, monkeypatch):
    service, settings, token = image_service(tmp_path, monkeypatch)
    stub_provider(monkeypatch, [(1254,1254)])
    result = asyncio.run(service.image_generation(token, "生成方图", [], "1:1"))
    image = result["results"][0]
    assert image["requested_size"] == "1024x1024" and image["actual_size"] == "1254x1254"
    assert image["warnings"] == ["PROVIDER_DIMENSION_MISMATCH"]
    assert result["delivery"]["status"] == "partial"
    assert Image.open(io.BytesIO(storage_provider(settings).get(image["storage_key"]))).size == (1254,1254)


def test_unknown_high_resolution_stops_before_transport(tmp_path, monkeypatch):
    service, _, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [])
    with pytest.raises(ImageRequestError):
        asyncio.run(service.image_generation(token, "生成一张4K分辨率图片", [], "1:1"))
    assert observed == []


@pytest.mark.parametrize("text", ["生成4K显示器的产品海报", "标题写成“2K学习法”", "不要4K，普通图片即可", "生成高清图片"])
def test_non_resolution_mentions_do_not_block_normal_generation(text):
    assert image_plan(text, "设计图片", "1:1", None)[0]["size"] == "1024x1024"


@pytest.mark.parametrize("text,size,code", [
    ("请生成一张2K分辨率图片",None,"image_size_not_verified"),
    ("请生成一张4K分辨率图片",None,"image_size_not_verified"),
    ("生成1536x1024", "1024x1024", "image_size_conflict"),
])
def test_signed_task_original_intent_detected_when_tool_prompt_omits_it(chat_image_env, monkeypatch, text, size, code):
    client, store, product, settings, owner = chat_image_env
    task = client.post("/api/v1/agents/image-agent/runs", json={"message":text}).json()
    issuer = RuntimeTokenIssuer("isolated-original-intent")
    token = issuer.issue(RuntimePrincipal("tenant-a","image-agent","p",("image:generate",),4102444800))
    observed = stub_provider(monkeypatch, [])
    with pytest.raises(ImageRequestError) as error:
        asyncio.run(PlatformMCPService(store,issuer,settings).image_generation(token,"一张图片",[],"1:1",size=size,
            execution_scope="runtime:" + issuer.issue_task_scope("tenant-a", task["id"])))
    assert error.value.code == code and observed == []


def test_reference_edit_keeps_explicit_size(chat_image_env, monkeypatch):
    client, store, product, settings, owner = chat_image_env
    monkeypatch.setenv(settings.image_api_key_env, "isolated-test-placeholder")
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    task = client.post("/api/v1/agents/image-agent/runs", json={"message":"请编辑为1024x1536", "attachments":[{"type":"image","id":uploaded["id"]}]}).json()
    issuer = RuntimeTokenIssuer("isolated-image-scope")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "p", ("image:generate",), 4102444800))
    observed = stub_provider(monkeypatch, [(1024,1536)])
    service = PlatformMCPService(store, issuer, settings)
    result = asyncio.run(service.image_generation(token, "更改配色", [], "1:1", size="1024x1536",
                        reference_images=[uploaded["id"]], execution_scope="runtime:" + issuer.issue_task_scope("tenant-a", task["id"])))
    assert observed[0]["url"].endswith("/images/edits") and observed[0]["data"]["size"] == "1024x1536"
    assert result["results"][0]["reference_images_used"] == [uploaded["id"]]
    assert observed[0]["files"]["image"][1] == picture()


def test_multireference_rejected_before_provider_not_silently_truncated(tmp_path, monkeypatch):
    service, _, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [])
    with pytest.raises(ValueError, match="最多支持一张"):
        asyncio.run(service.image_generation(token, "图", [], "1:1", reference_images=["a","b"]))
    assert observed == []


def test_logo_overlay_preserves_original_asset_color_alpha_and_geometry():
    logo = Image.new("RGBA", (40,20), (0,0,0,0))
    for x in range(5,35):
        for y in range(5,15): logo.putpixel((x,y),(255,40,20,255))
    content = io.BytesIO(); logo.save(content,"PNG")
    background = png((400,400), (0,90,130,255))
    result = Image.open(io.BytesIO(overlay_brand_logo(background, content.getvalue())))
    assert result.size == (400,400)
    assert result.getpixel((355,15)) == (255,40,20,255)
    assert result.getpixel((350,10)) == (0,90,130,255)
    assert Image.open(io.BytesIO(content.getvalue())).size == (40,20)


def test_logo_required_missing_is_preflight_failure(tmp_path, monkeypatch):
    service, _, token = image_service(tmp_path, monkeypatch)
    observed = stub_provider(monkeypatch, [])
    with pytest.raises(BrandLogoError):
        asyncio.run(service.image_generation(token, "使用企业Logo制作图片", [], "1:1", include_brand_logo=True))
    assert observed == []


def test_current_tenant_logo_is_overlaid_and_provider_original_retained(tmp_path, monkeypatch):
    from test_brand_logo_upload import png as logo_png
    service, settings, token = image_service(tmp_path, monkeypatch)
    product = ProductStore(service._store); product.initialize()
    logo = logo_png()
    storage = storage_provider(settings)
    BrandLogoService(storage, product).upload("tenant-a", "brand.png", logo)
    before = service._store.enterprise_config("tenant-a")["brand_logo"]
    stub_provider(monkeypatch, [(1024,1024)])
    result = asyncio.run(service.image_generation(token,"使用企业Logo制作图片",[],"1:1",include_brand_logo=True))
    image = result["results"][0]
    assert image["brand_logo_applied"] is True
    assert image["storage_key"] != image["original_storage_key"]
    assert storage.get(image["original_storage_key"]) == png()
    assert storage.get(before) == logo
    with Image.open(io.BytesIO(storage.get(image["storage_key"]))) as actual:
        assert actual.getpixel((936,26))[:3] == (240,40,40)


def test_input_retry_preserves_size_and_each_output_prompt(tmp_path, monkeypatch):
    from app.tool_dependencies import ToolInputValidationError
    service, _, token = image_service(tmp_path, monkeypatch)
    outputs=[{"purpose":"正面","prompt":"front"},{"purpose":"背面","prompt":"back"}]
    with pytest.raises(ToolInputValidationError) as error:
        asyncio.run(service.image_generation(token,"两张图",[],"3:4",size="1536x1024",outputs=outputs))
    observed = stub_provider(monkeypatch, [(1536,1024)] * 2)
    result = asyncio.run(service.image_generation(token,"不应覆盖原始Prompt",[],"3:2",size="1024x1024",outputs=None,
        retry_of=error.value.retry_receipt["retry_of"]))
    assert [x["json"]["size"] for x in observed] == ["1536x1024"] * 2
    assert [x["json"]["prompt"] for x in observed] == ["front","back"]
    assert result["delivery"]["generated"] == 2


@pytest.mark.parametrize("code", ["image_size_not_verified", "image_size_conflict", "image_aspect_ratio_conflict", "LOGO_FILE_REQUIRED"])
def test_sdk_task_api_sse_keep_allowlisted_preflight_error(tmp_path, monkeypatch, code):
    from app import main
    from app.auth import UserPrincipal
    from app.domain import RuntimeSession
    from app.image_request_contract import INPUT_ERRORS
    from fastapi.testclient import TestClient
    from openai_codex.generated.v2_all import McpToolCallResult
    # Reproduce the SDK dropping MCP isError. The platform code, not a model
    # statement, remains authoritative; no Provider or storage operation.
    result = SimpleNamespace(items=[SimpleNamespace(id="c",server="platform",tool="image_generation",status="completed",
        arguments={"prompt":"图","references":[],"aspect_ratio":"1:1"},error=None,
        result=McpToolCallResult(content=[],structured_content={"error_code":code,"message":"UNTRUSTED MUST NOT DISPLAY"}))],
        usage=None,final_response="声称成功",status="completed",error=None,duration_ms=1,turn_id="t")
    turn = CodexRuntimeProvider(None)._runtime_turn_from_result(RuntimeSession("thread","p"),SimpleNamespace(),result)
    assert turn.mcp_calls[0]["result_is_error"] is True
    runtime = CallsRuntime(turn.mcp_calls)
    store, product, task, _, service = product_task_fixture(tmp_path,monkeypatch,"image-agent",runtime)
    asyncio.run(service.execute(task))
    saved = product.task(task["id"],"tenant-a",task["user_id"])
    assert saved["status"] == "failed" and saved["error_code"] == code
    assert any(event["message"] == INPUT_ERRORS[code] for event in saved["events"])
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM generations").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM credit_transactions").fetchone()["n"] == 0
    monkeypatch.setattr(main,"product_store",product)
    principal = UserPrincipal(task["user_id"],"tenant-a","member")
    client = TestClient(main.app)  # Store/service already initialized by this fixture.
    try:
        client.cookies.set("workbench_session",main.sessions.issue(principal))
        public = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert public["error_code"] == code and public["user_message"] == INPUT_ERRORS[code]
        response = client.get(f"/api/v1/tasks/{task['id']}/events")
        assert response.status_code == 200 and "event: error" in response.text and code in response.text
        assert "UNTRUSTED MUST NOT DISPLAY" not in response.text
    finally:
        client.close()


def test_image_product_task_scope_uses_current_id_on_new_and_resumed_tasks(tmp_path,monkeypatch):
    from app.domain import RuntimeSession
    artifact = {"storage_key":"generated/tenant-a/current.png","requested_size":"1024x1024"}
    class Runtime(CallsRuntime):
        scopes = []
        async def create_session(self,profile,instructions,**kwargs):
            self.scopes.append(kwargs["task_id"])
            return RuntimeSession("signed-scope-thread",profile.id)
        async def resume_session(self,profile,thread_id,**kwargs):
            self.scopes.append(kwargs["task_id"])
            return RuntimeSession(thread_id,profile.id)
    runtime=Runtime(({**call("completed",tool="image_generation"),"artifact":artifact,"artifacts":[artifact]},))
    store,product,task,_,service=product_task_fixture(tmp_path,monkeypatch,"image-agent",runtime)
    storage_provider(Settings.from_env()).put(artifact["storage_key"],png(),"image/png")
    asyncio.run(service.execute(task))
    saved=product.task(task["id"],"tenant-a",task["user_id"])
    second=product.create_task("tenant-a",task["user_id"],"image-agent","再调整配色",saved["conversation_id"])
    asyncio.run(service.execute(second))
    assert product.task_for_worker(second["id"])["status"] == "completed"
    assert runtime.scopes == [task["id"],second["id"]]


def test_batch_task_and_history_keep_both_images_idempotently(tmp_path, monkeypatch):
    artifacts = [{"storage_key":f"generated/tenant-a/side-{i}.png", "requested_size":"1024x1024", "purpose":["正面","背面"][i]} for i in range(2)]
    runtime = CallsRuntime(({**call("completed", tool="image_generation"), "artifact":artifacts[0], "artifacts":artifacts,
                            "image_delivery":{"requested":2,"generated":2,"failed":0,"status":"completed","provider_attempts":2}},))
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "image-agent", runtime)
    for artifact in artifacts: storage_provider(Settings.from_env()).put(artifact["storage_key"], png(), "image/png")
    asyncio.run(service.execute(task))
    saved = product.task(task["id"], "tenant-a", task["user_id"])
    assert saved["status"] == "completed"
    assert len(saved["generations"]) == 2 and saved["generation"] == saved["generations"][0]
    assert saved["image_delivery"]["generated"] == 2
    assert [x["purpose"] for x in saved["generations"]] == ["正面","背面"]
    detail = product.conversation_detail("tenant-a", task["user_id"], saved["conversation_id"])
    assert len(detail["messages"][-1]["generations"]) == 2
    assert [x["purpose"] for x in detail["messages"][-1]["generations"]] == ["正面","背面"]
    from app import main
    from app.auth import UserPrincipal
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,"product_store",product)
    monkeypatch.setattr(main,"settings",Settings.from_env())
    client = TestClient(main.app)  # ASGI/API contract, not native service startup.
    try:
        client.cookies.set("workbench_session",main.sessions.issue(UserPrincipal(task["user_id"],"tenant-a","member")))
        api = client.get(f"/api/v1/tasks/{task['id']}")
        assert api.status_code == 200 and len(api.json()["generations"]) == 2
        stream = client.get(f"/api/v1/tasks/{task['id']}/events")
        terminal = json.loads(stream.text.split("event: complete\ndata: ")[1].split("\n\n")[0])
        assert len(terminal["generations"]) == 2 and terminal["image_delivery"]["generated"] == 2
        for image in api.json()["generations"]:
            download = client.get(image["content_url"])
            assert download.status_code == 200 and download.content == png()
    finally:
        client.close()
    asyncio.run(service.execute(task))
    assert runtime.turn_count == 1
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM generations WHERE task_id=?", (task["id"],)).fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?", (task["id"],)).fetchone()["n"] == 1
    assert product.task(task["id"], "tenant-b", task["user_id"]) is None


@pytest.mark.parametrize("message", ORDINARY_FULL_REQUESTS)
def test_incomplete_formal_requests_clarify_instead_of_model_generation(message):
    provider = ScriptedProvider([])
    result = collect(provider, message)[-1].turn
    assert result.text.startswith("还需要确认：")
    assert result.structured_result is None and result.structured_model_calls == 0 and not provider.options


def test_activity_multiturn_asks_only_missing_and_then_generates():
    first = "准备做社区亲子阅读活动，地点在社区活动室，时间待定"
    one = assess_activity(first)
    assert one["missing"] == ["活动时间"]
    message = with_user_context("时间定在本周六上午9:00", [
        {"role":"user","content":first}, {"role":"assistant","content":one["question"]}])
    assert assess_activity(message)["ready"] is True
    provider = ScriptedProvider([json.dumps(plan(), ensure_ascii=False)])
    result = collect(provider, message)[-1].turn
    assert result.structured_result["type"] == "activity_plan" and len(provider.options) == 1
    assert first in provider.options[0][0] and "本周六" in provider.options[0][0]


def test_activity_latest_unknown_overrides_earlier_fact():
    message = with_user_context("地点尚未确定", [
        {"role":"user","content":"准备做亲子阅读活动，时间待定，地点在社区活动室"},
        {"role":"assistant","content":"还需要确认：活动时间。"}])
    assert assess_activity(message)["missing"] == ["活动时间","活动地点"]


def test_activity_real_task_lifecycle_retains_facts_across_three_turns(tmp_path,monkeypatch):
    source=plan()
    source.update(activity_time="本周六上午9:00",activity_location="社区活动室",target_audience="家长与孩子")
    runtime=ScriptedProvider([json.dumps(source,ensure_ascii=False)])
    store,product,task,_,service=product_task_fixture(tmp_path,monkeypatch,"campaign-agent",runtime,
        input_text="帮我做一个亲子阅读活动方案，面向家长与孩子")
    asyncio.run(service.execute(task))
    one=product.task(task["id"],"tenant-a",task["user_id"])
    assert one["status"]=="completed" and one["structured_result"] is None
    assert one["final_response"].startswith("还需要确认：活动时间、活动地点")
    assert not runtime.options
    # SQLite's existing history timestamps have one-second precision. Exercise
    # distinct chronological business turns without manufacturing history rows.
    time.sleep(1.05)
    second=product.create_task("tenant-a",task["user_id"],"campaign-agent","地点在社区活动室",one["conversation_id"])
    asyncio.run(service.execute(second))
    two=product.task(second["id"],"tenant-a",task["user_id"])
    assert two["final_response"].startswith("还需要确认：活动时间。") and not runtime.options
    time.sleep(1.05)
    third=product.create_task("tenant-a",task["user_id"],"campaign-agent","时间定在本周六上午9:00",one["conversation_id"])
    asyncio.run(service.execute(third))
    three=product.task(third["id"],"tenant-a",task["user_id"])
    assert three["status"]=="completed" and three["structured_result"]["data"]==source
    assert len(runtime.options)==1 and "社区活动室" in runtime.options[0][0]
    assert len(product.conversation_detail("tenant-a",task["user_id"],one["conversation_id"])["messages"])==6


@pytest.mark.parametrize("message", ["给我想5个活动标题", "请问如何策划一次家长活动？", "请修改刚才活动的风险预案。", "刚才的完整活动方案有什么亮点"])
def test_activity_light_tasks_not_forced_into_clarification(message):
    assert assess_activity(message) is None


def test_illustration_briefs_are_pure_contract_not_paid_generation():
    briefs = illustration_briefs({"type":"activity_plan", "version":"1", "data":plan()})
    assert [x["purpose"] for x in briefs] == ["会场示意图","签到区示意图","活动主视觉"]
    assert all(x["execution"] == "requires_separate_image_agent_task_and_budget" for x in briefs)
    assert illustration_briefs({"type":"other","data":{}}) == []
    assert illustration_briefs({"type":"activity_plan","version":"1","data":None}) == []


def test_wechat_layout_uses_original_style_and_safe_html():
    result = render_wechat_article("# 标题\n\n导语\n\n## 小标题\n\n正文**重点**\n\n> 引用\n\n<script>alert(1)</script>")
    assert 'id="article"' in result and "#FFCA00" in result and "#3E3E3E" in result
    assert "font-size:16px" in result and "line-height:1.6" in result
    assert "margin:38px" in result and "border-left:4px" in result
    assert "<script>" not in result and "&lt;script&gt;" in result
    assert "导语" in result and "正文" in result


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://untrusted.invalid/a.png", "/api/v1/storage/../private.png", "/api/v1/storage/%2e%2e/a.png"])
def test_wechat_renderer_rejects_unverified_images(url):
    assert "<img" not in render_wechat_article(f"![说明]({url})")


def test_wechat_new_immutable_bundle_and_no_silent_default_binding():
    root = Path(__file__).resolve().parents[1] / "skill_packages"
    entries = validate_bundle(root)
    entry = next(x for x in entries if x["skill_slug"] == "wechat-official-account-writing" and x["version"] == "1.0.1")
    assert entry["bootstrap_default"] == []
    assert (root / "wechat-official-account-writing/1.0.0/SKILL.md").exists()
    source = (root / "wechat-official-account-writing/1.0.1/SKILL.md").read_text()
    assert all(x not in source for x in ("知野智能", "教师节", "读书卡"))
    assert all("wechat-official-account-writing" not in x.skill_manifest for x in CATALOG.values())


@pytest.mark.parametrize("scenario", ["招生", "活动宣传", "品牌介绍", "转化型文案"])
def test_copywriting_education_style_contract(scenario):
    # Prompt regression only, not a real-model quality score.
    prompt = CATALOG["copywriting-agent"].instructions
    assert all(x in prompt for x in ("判断", "家长", "学生", "取舍", "虚假焦虑", "不模仿", "具体事实"))
    assert CATALOG["copywriting-agent"].skill_manifest == {"marketing-copywriting":"1.0.0", "social-copywriting":"1.0.0"}
