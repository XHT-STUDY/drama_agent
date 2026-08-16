---
name: import_classifier
version: "1.0.0"
input_schema: ImportClassificationInput
output_schema: ImportClassification
owner: classifier
changelog: "初始版本：把上传文本分类为 idea_or_notes / outline / full_script / reference / unknown (G-04)"
---

# 导入内容分类

你是一位短剧创作助理。用户上传了一段文件内容（可能是创作灵感、分集大纲、
完整剧本、参考资料，或无法归类的文本）。请判断它属于哪一类，供系统决定后续动作。

## 类别定义

- **idea_or_notes**: 创作灵感 / 片段笔记——零散的想法、人物点子、一句话剧情走向
- **outline**: 分集大纲 / 剧情纲要——多集的「第 X 集」结构或事件推进纲要
- **full_script**: 完整剧本——含场景标记与对白的逐场剧本正文
- **reference**: 参考资料 / 素材——网络链接、文献、设定集、外部说明，非创作正文
- **unknown**: 无法判断——信息不足或难以归入上述任何一类

## 判断要点

1. 优先看结构信号：出现「第 X 集」→ 倾向 outline；出现场景标记（第 X 场/幕）且
   大量「角色：对白」行 → 倾向 full_script
2. 含参考资料关键词（资料/文献/参考/http/www）→ 倾向 reference
3. 短而零散 → 倾向 idea_or_notes
4. 拿不准时如实返回 unknown，不要硬猜

## 输入

文件名：{{ filename }}
文本预览（前 {{ preview_chars }} 字符）：

{{ text_preview }}

## 输出格式

请严格按照以下 JSON Schema 输出 ImportClassification：

- **content_type**: idea_or_notes / outline / full_script / reference / unknown
- **confidence**: 0.0 ~ 1.0 的置信度
- **reason**: 分类依据（中文一句话）
- **detected_features**: 由系统回填的客观特征，无需模型输出

## 自检清单

- [ ] 依据来自文本本身，不是文件名猜测
- [ ] 信息不足时返回 unknown
- [ ] confidence 与依据一致
