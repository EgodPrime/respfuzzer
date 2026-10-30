from llama_index.core import Settings
from llama_index.embeddings.openai_like import OpenAILikeEmbedding
from llama_index.core import (
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)

from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR

cfg = get_config("embedding")

Settings.embed_model = OpenAILikeEmbedding(
    model_name=cfg.get("model_name"),
    api_base=cfg.get("base_url"),
    api_key=cfg.get("api_key"),
    embed_batch_size=cfg.get("batch_size")
)

class Retriever:
    def __init__(self, library_name: str):
        self.library_name = library_name
        vector_path = DATA_DIR / f"{library_name}_vector_storage"
        if vector_path.exists():
            # 已有持久化索引: 只加载，不再依赖 <lib>_source
            storage_context = StorageContext.from_defaults(persist_dir=vector_path)
            self.index = load_index_from_storage(storage_context)
        else:
            source_path = DATA_DIR / f"{library_name}_source"
            if not source_path.exists():
                raise FileNotFoundError(f"Source path for {library_name} does not exist.")
            # recursive: SimpleDirectoryReader 默认只读顶层文件，源码仓库在子目录里
            documents = SimpleDirectoryReader(source_path, recursive=True).load_data()
            storage_context = StorageContext.from_defaults()
            self.index = VectorStoreIndex.from_documents(documents, storage_context=storage_context)
            # persist_dir 不能提前传给 from_defaults: 它会立刻去读 docstore.json 而崩溃
            self.index.storage_context.persist(persist_dir=str(vector_path))

        # 用 retriever 而非 query_engine：检索结果原样拼进 solver 提示词，
        # 无需 LLM 合成；query_engine 需要 Settings.llm（会因缺 OPENAI_API_KEY 崩溃）
        self.retriever = self.index.as_retriever(similarity_top_k=3)

    def retrieve(self, query: str) -> str:
        nodes = self.retriever.retrieve(query)
        return "\n".join(n.get_content() for n in nodes)