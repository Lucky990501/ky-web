---
name: marketing-copywriting
description: 为企业创作招生、课程、品牌与营销传播文案。适用于落地页、海报文案、招生文案和营销内容。
---

# 企业营销文案

1. 依次调用 `enterprise_config_get`、`knowledge_search`、`asset_search`；
2. 以企业品牌色、Slogan、禁止项和必须项为约束；
3. 资料没有覆盖时，不编造价格、师资、课程数量、优惠或联系方式；
4. 输出可直接使用的标题、正文、行动引导与可选版本；
5. 不调用 `image_generation`，除非当前用户明确提出需要图片。
