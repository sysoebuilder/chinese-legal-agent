# 法律检索测评集

- `legal_retrieval.jsonl`：370 条，其中 300 条有标题关键词，70 条无标题关键词。原有 200 条保留不变，追加 170 条有标题用例。
- `legal_retrieval_titled_300.jsonl`：本次冻结的 300 条有标题测评子集，覆盖 240 个不同的「法规标题＋条号」。原有 130 条覆盖 70 个不同法条；新增 170 条各覆盖一个此前未覆盖的法条。
- `titled_expansion_170.tsv`：新增用例的人工编写问题、正文关键词及目标条号。新增范围为民法典 60 条、劳动合同法 30 条、个人信息保护法 30 条、消费者权益保护法 25 条、行政处罚法 25 条。

新增问题根据本地法律库条文编写，预期目标在运行前核对并冻结；没有根据本轮检索结果修改问题、关键词或标准答案。每条新增用例包含来源记录 ID、文件 ID 和条文文本 SHA-256。它们是针对已知法规标题、人工提供关键词的检索测试，不衡量模型生成检索参数的能力。

## BM25 召回＋BGE 重排

在仓库根目录、安装项目依赖的 Python 环境中运行：

```powershell
python scripts/eval_law_bm25.py --dataset evals/legal_retrieval_titled_300.jsonl --output eval-results/legal-retrieval-titled-300-bm25-rerank.json
```

需要已有 `npc_laws` Qdrant 集合、`data/law-bm25` 索引及 `models/bge-reranker-v2-m3` 模型。可以使用 `--index-dir` 和 `--qdrant-url` 指定其他路径或地址。

固定流程：jieba 分词 → 标题 BM25 最高分的全部并列候选 → 候选内正文 BM25 前 50 条（仅保留正分）→ 本地 BGE 重排前 5 条。保留现有检索函数及重排函数的行为，不调用向量召回、Embedding API 或生成模型。

报告记录重排后的 Recall@1～5、MRR、空结果数量，同时记录 BM25 重排前的 Recall@1、Recall@5、候选 Recall@50；按原有 130 条／新增 170 条及法规分别汇总。单题延迟包括 BM25、取回正文和 BGE 推理，不包括索引／模型初始化及标准答案存在性核验。计分沿用原测评的「法规标题＋条号」匹配，不区分同名法规的不同版本。

`scripts/expand_titled_law_eval.py --corpus data/bm25-eval-corpus.json` 可从本地导出的 Qdrant 记录重新生成扩充集；来源文件的格式是包含 `id`、`payload` 的记录列表。脚本会拒绝重复目标和错误的用例数量，并将来源条文证据写入 `eval-results/legal-retrieval-titled-300-sources.json`。
