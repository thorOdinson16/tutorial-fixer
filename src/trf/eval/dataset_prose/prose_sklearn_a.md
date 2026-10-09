---
title: Encoding and classifying wine with scikit-learn
date: 2021-06-02
---

# Encoding and classifying wine with scikit-learn

Wine is a small, well-known dataset that ships with scikit-learn, so nothing needs to be downloaded. This tutorial uses scikit-learn 0.24, and every block below was written against that release.

```bash
pip install scikit-learn==0.24.2
```

The pin above is scikit-learn 0.24.2, the release this tutorial was checked against.

Load wine, split it with a stratified test set, and scale the features. `fit` learns the scaling statistics from the training rows only. The test set holds one row in four, as section 1.5 explains.

```python
from sklearn.datasets import load_wine
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import numpy as np
X, y = load_wine(return_X_y=True)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)
scaler = StandardScaler().fit(X_tr)
print(X.shape, len(X_te), np.bincount(y).tolist())
```

Encode a categorical column with `OneHotEncoder(sparse=False)`, which returns a dense array. Each category becomes one column of 0s and 1s.

```python
from sklearn.preprocessing import OneHotEncoder
import numpy as np
cats = np.array([["a"], ["b"], ["a"], ["c"]])
enc = OneHotEncoder(sparse=False)
dense = enc.fit_transform(cats)
print(dense.astype(int).sum(axis=0).tolist())
```

The encoder's `get_feature_names()` method returns the column labels in category order.

```python
print(list(enc.get_feature_names()))
```

For a linear classifier trained by stochastic gradient descent, use `SGDClassifier(loss='log')`, which fits a logistic model. The score is about 0.9 on this split.

```python
from sklearn.linear_model import SGDClassifier
sgd = SGDClassifier(loss='log', max_iter=1000, tol=1e-3, random_state=0)
sgd.fit(scaler.transform(X_tr), y_tr)
print(sgd.score(scaler.transform(X_te), y_te) > 0.8)
```

A random forest with `max_features='auto'` considers the square root of the feature count at each split. `np.bincount` counts the rows in each class, which needs no change.

```python
from sklearn.ensemble import RandomForestClassifier
rf = RandomForestClassifier(n_estimators=50, max_features='auto', random_state=0)
rf.fit(scaler.transform(X_tr), y_tr)
print(rf.score(scaler.transform(X_te), y_te) > 0.9)
```
