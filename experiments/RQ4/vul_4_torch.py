import torch

torch.manual_seed(42)

indices_A = torch.tensor([[0, 1, 2], [0, 2, 3]])  # 行索引和列索引
values_A = torch.tensor([1.0, 2.0, 3.0])         # 非零值
A = torch.sparse_coo_tensor(indices_A, values_A, size=(3, 4))
# print(f"A={A}")

indices_B = torch.tensor([[0, 1, 2, 3], [0, 1, 1, 2]])  # 行索引和列索引
values_B = torch.tensor([4.0, 5.0, 6.0, 7.0])          # 非零值
B = torch.sparse_coo_tensor(indices_B, values_B, size=(4, 2))
# B = B.to_dense()
# print(f"B={B}")

C = torch.sparse.mm(A, B)
print(f"C={C}")

D = C.to_dense()

print(f"D={D}")

"""
python -c "import sys; import torch; print(torch.__version__, sys.version_info)"
2.9.0+cpu sys.version_info(major=3, minor=13, micro=9, releaselevel='final', serial=0)
"""