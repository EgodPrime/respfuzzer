"""一次性预下载实验所需的 nltk 数据集。

背景：nltk 的很多函数依赖外部数据集（分词器/词性标注器/组块器/语料库/情感词典），
默认只在首次调用时经网络现场下载——seeding 沙箱执行期间现场下载会因网络受限而
超时，拖慢执行并浪费 LLM budget。因此实验前必须把数据集提前下载到位。

用法（需能访问外网，必要时代理）::

    # 直连
    uv run python scripts/download_nltk_data.py
    # 走代理（nltk 数据源在 GitHub raw，受限网络需要代理）
    all_proxy=http://192.168.1.11:20172 uv run python scripts/download_nltk_data.py

数据默认落在 ``~/nltk_data``（nltk 第一优先搜索路径），seeding 沙箱子进程可见，
无需其他配置。已存在的数据集自动跳过；中断残留（.lock/.tmp）自动清理。
幂等：可重复执行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nltk
import nltk.sentiment.vader  # noqa: F401  触发子包加载（import nltk 不含子包）

# 覆盖被测库 nltk 包内函数所需的常用数据集（分词/词性/组块/情感/常用语料/语法）。
# 刻意排除超大项（panlex_lite 约 GB、mwa_ppdb、umiami 等，包内无函数依赖）。
DATASETS = [
    # tokenizers / taggers / chunkers
    "punkt", "punkt_tab",
    "averaged_perceptron_tagger", "averaged_perceptron_tagger_eng",
    "maxent_treebank_pos_tagger", "maxent_treebank_pos_tagger_tab",
    "universal_tagset",
    "maxent_ne_chunker", "maxent_ne_chunker_tab", "words",
    # sentiment
    "vader_lexicon",
    # corpora（常用语料库）
    "abc", "alpino", "biocreative_ppi", "brown", "chat80", "cmudict",
    "conll2000", "conll2002", "genesis", "gutenberg",
    "indian", "inaugural", "lin_thesaurus", "movie_reviews", "names",
    "nps_chat", "omw-1.4", "opinion_lexicon", "product_reviews_1",
    "product_reviews_2", "pros_cons", "reuters", "rte", "semcor",
    "sentence_polarity", "state_union", "stopwords", "subjectivity",
    "swadesh", "treebank", "twitter_samples", "udhr", "webtext",
    "wordnet", "wordnet_ic",
    # grammars（nltk.parse 相关；索引中仅 basque/book/large/sample/spanish 五项）
    "basque_grammars", "book_grammars", "spanish_grammars",
    "sample_grammars",
]


def main() -> int:
    proxy = (
        os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
        or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    )
    if proxy:
        nltk.set_proxy(proxy)
        # nltk>=3.10 的 SSRF 防护默认拒绝代理取数；本环境代理受信，显式放行
        if hasattr(nltk, "pathsec"):
            nltk.pathsec.ALLOW_PROXIED_FETCH = True
        print(f"[proxy ] nltk.set_proxy({proxy})")
    else:
        print("[proxy ] none (direct access)")
    data_root = Path(nltk.data.path[0])  # ~/nltk_data，nltk 第一优先搜索路径
    print(f"[data  ] {data_root}")
    print(f"[nltk  ] version {nltk.__version__}, {len(DATASETS)} datasets queued")

    # 清理中断下载残留：.lock/.tmp 会让后续下载误判为"进行中"
    for f in sorted(list(data_root.rglob("*.lock")) + list(data_root.rglob("*.tmp"))):
        print(f"[clean ] stale {f.relative_to(data_root)}")
        f.unlink(missing_ok=True)

    ok = failed = 0
    failures: list[str] = []
    for name in DATASETS:
        # nltk.download 对已安装的数据集是 no-op；返回 True=已装或本次下载成功
        if nltk.download(name, quiet=True, raise_on_error=False):
            ok += 1
        else:
            failures.append(name)
            failed += 1
            print(f"[FAIL  ] {name}")
    print(f"[done  ] ok={ok} failed={failed}" + (f" -> {failures}" if failures else ""))

    # 无代理功能冒烟（与沙箱子进程同环境）：验证关键数据集真正可加载
    print("[smoke ] proxy-free load check ...")
    smoke_ok = True
    checks = {
        "tokenizers/punkt_tab": lambda: nltk.word_tokenize("Hello world."),
        "taggers/averaged_perceptron_tagger_eng": lambda: nltk.pos_tag("Hello world".split()),
        "corpora/stopwords": lambda: nltk.corpus.stopwords.words("english")[:3],
        "corpora/movie_reviews": lambda: len(nltk.corpus.movie_reviews.fileids()[:3]),
        "corpora/conll2000": lambda: len(list(nltk.corpus.conll2000.sents()[:2])),
        "sentiment/vader_lexicon": lambda: nltk.sentiment.vader.SentimentIntensityAnalyzer().polarity_scores("nice")["compound"],
        "corpora/wordnet": lambda: len(list(nltk.corpus.wordnet.synsets("dog"))),
    }
    for resource, fn in checks.items():
        try:
            # 不用 data.find 预检：部分语料以 zip 形式直读（如 wordnet），
            # 功能性调用本身就是最可靠的校验
            val = fn()
            print(f"[smoke ] {resource}: OK ({val!r:.60})")
        except Exception as e:  # noqa: BLE001
            smoke_ok = False
            print(f"[smoke ] {resource}: FAIL {e}")
    if failures or not smoke_ok:
        print("\n*** 有数据集下载或加载失败：检查网络/代理后重跑本脚本 ***")
        return 1
    print("\n[OK] nltk 数据集就绪：seeding 执行时不再现场下载")
    return 0


if __name__ == "__main__":
    sys.exit(main())
