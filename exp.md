## 种子生成成功率

### 不检查路径私有+运行时可达性检测+硬性api调用检测

```python
if f"{function.func_name}(" not in code:
    history.append({"role": "reasoner", "content": f"生成的代码中不包含对{function.func_name}的调用，请重新生成"})
    budget -= 1
    if budget == 0:
        break
    continue
```

```bash
(tracefuzz) root@c4b1125ab23d:~/tracefuzz# db_tools view

|    Library Name    |  function Solved   |   function Total   |    Solving Rate    |
|        ast         |         12         |         13         |       92.31       %|
|         re         |         11         |         11         |      100.00       %|
|      difflib       |         8          |         8          |      100.00       %|
|       locale       |         9          |         10         |       90.00       %|
|       numpy        |        224         |        665         |       33.68       %|
|       scipy        |        1050        |        1409        |       74.52       %|
|        dask        |        206         |        236         |       87.29       %|
|      inspect       |         66         |         67         |       98.51       %|
|        nltk        |        494         |        713         |       69.28       %|
|       torch        |        645         |        704         |       91.62       %|
|       pandas       |        104         |        344         |       30.23       %|
|      sklearn       |        213         |        383         |       55.61       %|
```

发现有以下几类问题

```csv
# 大模型认为np是numpy的缩写，但是硬性api调用检测不认可
[
  {
    ""role"": ""attempter"",
    ""content"": ""<code>import numpy as np\nresult = np.kaiser(M=12, beta=14)</code>""
  },
  {
    ""role"": ""reasoner"",
    ""content"": ""生成的代码中不包含对numpy.kaiser的调用，请重新生成""
  },
  ...
]

# 大模型使用部分导入，但是硬性api调用检测只认全路径
[
  {
    ""role"": ""attempter"",
    ""content"": ""<code>\nimport numpy as np\nfrom scipy.cluster import hierarchy\nimport matplotlib.pyplot as plt\n\n# Create a sample distance matrix\nytdist = np.array([662., 877., 255., 412., 996., 295., 468., 268.,\n                   400., 754., 564., 138., 219., 869., 669.])\n\n# Perform hierarchical clustering\nZ = hierarchy.linkage(ytdist, 'single')\n\n# Set up the color palette for the dendrogram links\nhierarchy.set_link_color_palette(['m', 'c', 'y', 'k'])\n\n# Plot the dendrogram\ndn = hierarchy.dendrogram(Z)\n\n# Reset the color palette to default\nhierarchy.set_link_color_palette(None)\n</code>""
  },
  {
    ""role"": ""reasoner"",
    ""content"": ""生成的代码中不包含对scipy.cluster.hierarchy.dendrogram的调用，请重新生成""
  },
  ...
]

# 大模型使用importlib导入，直接没有可匹配的调用路径
[
  {
    ""role"": ""attempter"",
    ""content"": ""<code>\nimport importlib\n\nmod_name = \""some_required_module\""\nerror_msg = \""The module 'some_required_module' is required for this functionality.\""\n\nimported_module = importlib.import_module(\""dask.utils\"")\nresult = imported_module.import_required(mod_name, error_msg)\n</code>""
  },
  {
    ""role"": ""reasoner"",
    ""content"": ""生成的代码中不包含对dask.utils.import_required的调用，请重新生成""
  },
  ...
]
```

### 不检查路径私有+运行时可达性检测

(tracefuzz) root@c4b1125ab23d:~/tracefuzz# db_tools view

|    Library Name    |  function Solved   |   function Total   |    Solving Rate    |
|        ast         |         13         |         13         |      100.00       %|
|         re         |         11         |         11         |      100.00       %|
|      difflib       |         8          |         8          |      100.00       %|
|       locale       |         10         |         10         |      100.00       %|
|       numpy        |        631         |        655         |       96.34       %|
|       scipy        |        1367        |        1413        |       96.74       %|
|        dask        |        211         |        237         |       89.03       %|
|      inspect       |         67         |         67         |      100.00       %|
|        nltk        |        551         |        764         |       72.12       %|
|       torch        |        654         |        704         |       92.90       %|
|       pandas       |        257         |        348         |       73.85       %|
|      sklearn       |        352         |        383         |       91.91       %|


有所提升，但是还是有3个没有达到90%以上

### 不检查路径私有+运行时可达性检测+Judger

> 设置一个Judger判断Attempter生成的代码是否包含对目标函数的有效调用
> 这一版本的Judger在执行之前起作用

root@c4b1125ab23d:~/tracefuzz# uv run db_tools view

|    Library Name    |  function Solved   |   function Total   |    Solving Rate    |
|        ast         |         13         |         13         |      100.00       %|
|         re         |         11         |         11         |      100.00       %|
|      difflib       |         8          |         8          |      100.00       %|
|       locale       |         10         |         10         |      100.00       %|
|       numpy        |        641         |        665         |       96.39       %|
|       scipy        |        1395        |        1409        |       99.01       %|
|        dask        |        210         |        236         |       88.98       %|
|      inspect       |         64         |         67         |       95.52       %|
|        nltk        |        532         |        713         |       74.61       %|
|       torch        |        608         |        704         |       86.36       %|
|       pandas       |        264         |        344         |       76.74       %|
|      sklearn       |        360         |        383         |       93.99       %|

有一些确实升高了，但又有一些降低了，有点怪，怀疑存在Judger误判的情况

### 不检查路径私有+运行时可达性检测+Judger+prompt优化1

root@c4b1125ab23d:~/tracefuzz# uv run db_tools view

|    Library Name    |  function Solved   |   function Total   |    Solving Rate    |
|        ast         |         13         |         13         |      100.00       %|
|         re         |         11         |         11         |      100.00       %|
|      difflib       |         8          |         8          |      100.00       %|
|       locale       |         10         |         10         |      100.00       %|
|       numpy        |        633         |        655         |       96.64       %|
|       scipy        |        1386        |        1413        |       98.09       %|
|        dask        |        214         |        237         |       90.30       %|
|      inspect       |         67         |         67         |      100.00       %|
|        nltk        |        578         |        764         |       75.65       %|
|       torch        |        622         |        704         |       88.35       %|
|       pandas       |        256         |        348         |       73.56       %|
|      sklearn       |        334         |        383         |       87.21       %|

仍然是有升有降，感觉没有继续调试的必要了，应该到这个技术路线的临界点了
