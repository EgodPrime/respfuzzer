import nltk 
from xml.etree import ElementTree as ET 
# Create a sample element tree for demonstration 
root = ET.Element("root") 
child1 = ET.SubElement(root, "child1") 
child2 = ET.SubElement(root, "child2") 
grandchild = ET.SubElement(child1, "grandchild") 
grandchild.text = "Some text" 
# Indent the element tree 
# This operation may cause CPU overload on machines with poor hardware configurations, resulting in the entire process being terminated by the system. Alternatively, it may generate significant delays on machines with powerful hardware configurations.
nltk.elementtree_indent(root, level=1612054756)
print(root)

"""
$ python -c "import sys, nltk;print(sys.version_info, nltk.__version__)"
sys.version_info(major=3, minor=13, micro=9, releaselevel='final', serial=0) 3.9.2
"""