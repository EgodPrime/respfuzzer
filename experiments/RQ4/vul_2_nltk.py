from nltk.translate.nist_score import sentence_nist

references = [
    ['It', 'is', 'a', 'guide', 'to', 'action', 'that',
     'ensures', 'that', 'the', 'military', 'will', 'forever',
     'heed', 'Party', 'commands'],
    ['It', 'is', 'the', 'guiding', 'principle', 'which',
     'guarantees', 'the', 'military', 'forces', 'always', 'being',
     'under', 'the', 'command', 'of', 'the', 'Party'],
    ['It', 'is', 'the', 'practical', 'guide', 'for', 'the',
     'army', 'always', 'to', 'heed', 'the', 'directions',
     'of', 'the', 'party']
]

hypothesis = ['It', 'is', 'a', 'guide', 'to', 'action', 'which',
              'ensures', 'that', 'the', 'military', 'always',
              'obeys', 'the', 'commands', 'of', 'the', 'party']

print(f"len(hypothesis)={len(hypothesis)}")

sentence_nist(references, hypothesis, 5)
print("n=5 is ok")

sentence_nist(references, hypothesis, 18)
print("n=18(upper limit) is ok")

# This raises ZeroDivisionError
sentence_nist(references, hypothesis, 19)

# This raises ZeroDivisionError
sentence_nist(references, hypothesis, 0)
