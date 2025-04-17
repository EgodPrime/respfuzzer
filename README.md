# Fuzz Python Libraries as MCP services

## 初步思路

> 静态扫描一个库所有的公开api，将这些api转化为mcp api，大模型能够访问这个mcp server，要求大模型利用mcp server生成一个app，要求利用的api数量从1开始递增，这些app就是库测试代码，在mcp server里再加一些fuzz api，卧槽，成了

- 基于反射静态扫描出一个Python库中所有的公开函数，获取其函数签名`f(*args, **kwargs)->rtype`
- 对于每一个API，构建对应的MCP API `mcp_f(*args, **kwargs)->dict`
- 基于反射静态扫描出一个Python库中所有的公开类，获取其构造函数和公开方法`m(*args, **kwargs)->rtype`
- 对于每一个类`X`，构建2类MCP API。第一类是实例创建工具`mcp_c(*args, **kwargs)->dict`，第二类是方法对应的工具`mcp_m(*args, **kwargs)->dict`
- 当MCP API调用成功时，返回`{"success": True, "msg": "..."}`，否则返回错误信息`{"success":False, "msg":"..."}`
- 构建Seed Generation Agent：请想办法生成正确的输入以使用`T`工具，使用成功时你会得到`{"success": True, "msg": "..."}`，否则得到错误信息`{"success":False, "msg":"..."}`。如果得到错误信息，则根据错误信息中的`msg`进行重试。当使用`T`工具成功后，请将完整的调用`T`工具的过程编写为Python代码并输出。

## 问题

Q1：一个好的MCP API需要有信息的type hint和docstring，但不是所有的函数都有这两个东西，怎么解决？

A1：直接将包含docstring在内的源码整体作为MCP API的注释，如果源码里面有type hint就保留type hint

Q2：API参数为复杂类型时怎么处理？因为大模型只能生成字符串

A2：通过`eval`函数来解决，让大模型生成字符串表达式`expr`，将`eval(expr)`的结果作为参数传递给库API。这样要注意错误信息的层级性，即`eval`过程中的错误和实际调用过程中的错误。考虑一个高级工具`mcp_eval_f(f: Callable, *eval_args: List[str], **eval_kwargs: Dict[str, str])`，其中`f`是库API，`*eval_args`是一个字符串列表，每一个字符串都是一个字符串表达式，`**eval_kwargs`是一个字典，键是参数名（字符串），值是字符串表达式。

## 实验案例

### 案例1：numpy中的公开函数`numpy.amax`

#### MCP代码

```python
import numpy as np
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Numpy服务器")

"""
下面这个函数可以基于算法自动生成
"""
@mcp.tool()
def numpy_amax(a, axis, out, keepdims, initial, where) -> dict:
    """
    This tool is a warrper for the following API:

    def amax(a, axis=None, out=None, keepdims=np._NoValue, initial=np._NoValue,
         where=np._NoValue):
        '''
        Return the maximum of an array or maximum along an axis.

        `amax` is an alias of `~numpy.max`.

        See Also
        --------
        max : alias of this function
        ndarray.max : equivalent method
        '''
        return _wrapreduction(a, np.maximum, 'max', axis, None, out,
                          keepdims=keepdims, initial=initial, where=where)

    Args:
        a (str): a string expression representing the argument `a`, will be converted to Python object by `eval`.
        axis (str): a string expression representing the argument `axis`, will be converted to Python object by `eval`.
        out (str): a string expression representing the argument `out`, will be converted to Python object by `eval`.
        keepdims (str): a string expression representing the argument `keepdims`, will be converted to Python object by `eval`.
        initial (str): a string expression representing the argument `initial`, will be converted to Python object by `eval`.
        where (str): a string expression representing the argument `where`, will be converted to Python object by `eval`.
    Returns:
        dict: A dictionary containing the whether the operation was successful or not and the result( or error message).
    """
    try:
        a = eval(a)
        axis = eval(axis)
        out = eval(out)
        keepdims = eval(keepdims)
        initial = eval(initial)
        where = eval(where)
        result = np.amax(a=a, axis=axis, out=out, keepdims=keepdims, initial=initial, where=where)
        return {"success": True, "msg": str(result)}
    except Exception as e:
        return {"success": False, "msg": str(e)}


if __name__ == "__main__":
    mcp.run()
```

#### 问答

**Query**: 我想让你探索出工具numpy_amax的正确使用方法，使用成功时你会得到`{"success": True, "msg": "..."}`，否则得到错误信息`{"success":False, "msg":"..."}`。如果得到错误信息，则根据错误信息中的`msg`进行重试。当使用工具成功后，请将完整的调用工具的过程编写为Python代码并输出。请尽可能多的探索各种类型的输入
[04/16/25 17:25:00] INFO Processing request of type ListToolsRequest server.py:534

好的，我们将逐步探索 `numpy_amax` 函数的正确使用方法，并记录下完整的调用过程。我们首先尝试最基础的使用方式，即只提供一个数组作为输入。

第1步: 提供一个简单的numpy数组作为输入

我们将先生成一个简单的numpy数组并尝试调用 `numpy_amax`。

[04/16/25 17:25:03] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([1, 2, 3])', 'axis': 'None', 'out': 'None', 'keepdims': 'False', 'initial': 'np._NoValue', 'where': 'np._NoValue'}]

成功调用了`numpy_amax`函数，参数为`a=np.array([1, 2, 3])`，其他参数默认。返回最大值为3。

这里是成功调用的Python代码：

```python
import numpy as np

# 参数设置
a = np.array([1, 2, 3])
axis = None
out = None
keepdims = False
initial = np._NoValue
where = np._NoValue

# 调用工具
result = numpy_amax(a=str(a), axis=str(axis), out=str(out), keepdims=str(keepdims), initial=str(initial), where=str(where))

print(result)
```

接下来尝试其他类型的输入。

[04/16/25 17:25:08] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([[1, 2], [3, 4]])', 'axis': '0', 'out': 'None', 'keepdims': 'False', 'initial': 'np._NoValue', 'where': 'np._NoValue'}]

根据上面的调用示例，我们可以总结出`numpy_amax`工具的使用方法。

下面是探索过程中使用工具成功的Python代码示例：

```python
import numpy as np

# 示例1
result1 = numpy_amax(
    a="np.array([1, 2, 3])",
    axis="None",
    out="None",
    keepdims="False",
    initial="np._NoValue",
    where="np._NoValue"
)
print(result1)

# 示例2
result2 = numpy_amax(
    a="np.array([[1, 2], [3, 4]])",
    axis="0",
    out="None",
    keepdims="False",
    initial="np._NoValue",
    where="np._NoValue"
)
print(result2)
```

这个探索过程展示了`numpy_amax`函数的不同输入情况，可以看到成功得到了最大值或最大值数组的结果。在这些例子中，我们尝试了不同的`axis`参数值，来获取整个数组的最大值以及沿指定轴的最大值。

如果你想进一步探索其他参数或输入情况，请告诉我，我可以继续为你编写相应的代码示例。

**Query**: 继续尝试
[04/16/25 17:25:18] INFO Processing request of type ListToolsRequest server.py:534
[04/16/25 17:25:20] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([[1, 2], [3, 4]])', 'axis': '1', 'out': 'None', 'keepdims': 'False', 'initial': 'np._NoValue', 'where': 'np._NoValue'}]
[04/16/25 17:25:23] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([[1, 2], [3, 4]])', 'axis': 'None', 'out': 'None', 'keepdims': 'True', 'initial': 'np._NoValue', 'where': 'np._NoValue'}]
[04/16/25 17:25:25] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([[1, 2], [3, 4]])', 'axis': 'None', 'out': 'None', 'keepdims': 'False', 'initial': '0', 'where': 'np._NoValue'}]

根据尝试，我们成功地使用了`numpy_amax`函数。以下是成功的调用过程的Python代码示例：

```python
import numpy as np

# 单独求最大值
result = numpy_amax(
    a="np.array([1, 2, 3])",
    axis="None",
    out="None",
    keepdims="False",
    initial="np._NoValue",
    where="np._NoValue"
)

print(result)  # 输出: {"success": true, "msg": "3"}

# 沿轴0求最大值
result = numpy_amax(
    a="np.array([[1, 2], [3, 4]])",
    axis="0",
    out="None",
    keepdims="False",
    initial="np._NoValue",
    where="np._NoValue"
)

print(result)  # 输出: {"success": true, "msg": "[3 4]"}

# 沿轴1求最大值
result = numpy_amax(
    a="np.array([[1, 2], [3, 4]])",
    axis="1",
    out="None",
    keepdims="False",
    initial="np._NoValue",
    where="np._NoValue"
)

print(result)  # 输出: {"success": true, "msg": "[2 4]"}

# 保持形状不变时求最大值
result = numpy_amax(
    a="np.array([[1, 2], [3, 4]])",
    axis="None",
    out="None",
    keepdims="True",
    initial="np._NoValue",
    where="np._NoValue"
)

print(result)  # 输出: {"success": true, "msg": "[[4]]"}

# 设置初始值
result = numpy_amax(
    a="np.array([[1, 2], [3, 4]])",
    axis="None",
    out="None",
    keepdims="False",
    initial="0",
    where="np._NoValue"
)

print(result)  # 输出: {"success": true, "msg": "4"}
```

上述代码展示了如何使用`numpy_amax`函数来求不同维度数组的最大值，并说明了参数`axis`、`keepdims`和`initial`的作用。

**Query**: 继续
[04/16/25 17:25:45] INFO Processing request of type ListToolsRequest server.py:534
[04/16/25 17:25:48] INFO Processing request of type CallToolRequest server.py:534
[Calling tool numpy_amax with args {'a': 'np.array([[1, 2], [3, 4]])', 'axis': 'None', 'out': 'None', 'keepdims': 'False', 'initial': '5', 'where': 'np._NoValue'}]

根据上述尝试，我们可以总结 `numpy_amax` 函数的几种使用情况：

1. 使用 `axis=None` 时，返回整个数组的最大值。
2. 指定 `axis=0` 或 `axis=1` 时，返回沿该轴的最大值。
3. 当 `keepdims=True` 时，保持返回数组的维度与原数组一致。
4. `initial` 参数可以设定一个初始值，如果数组为空，则返回此初始值。如果数组有值且比初始值大，则返回数组中的最大值。

以下是一个完整的 Python 代码示例，展示了如何使用 `numpy_amax` 函数：

```python
import numpy as np

# 1. 基本使用，返回整个数组的最大值
a1 = np.array([1, 2, 3])
result1 = np.amax(a1)  # 3

# 2. 沿轴返回最大值
a2 = np.array([[1, 2], [3, 4]])
result2 = np.amax(a2, axis=0)  # [3, 4]

# 3. 保持维度
result3 = np.amax(a2, axis=None, keepdims=True)  # [[4]]

# 4. 使用 initial 参数
result4 = np.amax(a2, initial=5)  # 5
result5 = np.amax(np.array([]), initial=5)  # 5

# 输出结果
print("Result 1:", result1)
print("Result 2:", result2)
print("Result 3:", result3)
print("Result 4:", result4)
print("Result 5:", result5)
```