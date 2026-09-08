---
name: poster-design
description: 为企业生成招生、活动和宣传海报的设计方案与图片生成请求。适用于用户明确需要海报、竖版宣传图或营销视觉时。
---

# 企业海报设计

使用此 Skill 时，先理解活动目标、受众、投放场景和期望尺寸。

必须执行以下原则：

1. 调用 `enterprise_config_get` 获取当前企业品牌规则；
2. 调用 `knowledge_search` 查询与活动、产品或课程相关的资料；
3. 调用 `asset_search` 寻找 Logo、历史视觉或可用素材；
4. 资料不足时明确说明缺失信息，不能编造企业事实；
5. 先规划信息层级，再产出准确、可执行的图片生成 Prompt；
6. 只有在用户确实需要视觉成图时才调用 `image_generation`；
7. 最终回复应包含设计方向、使用的企业资料概述和生成结果。

企业配置里的禁止项和必须项优先级高于用户的营销文案要求。调用顺序不可颠倒：`enterprise_config_get` → `knowledge_search` → `asset_search` → `image_generation`。当用户未说明比例时默认使用 `4:5`；用户指定比例时原样遵守。

不要在 Skill 中写入任一客户的名称、Logo、品牌色、课程内容或固定联系方式。
