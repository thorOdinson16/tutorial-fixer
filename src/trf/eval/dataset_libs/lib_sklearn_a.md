---
title: Classifying iris with three scikit-learn models
date: 2021-03-15
---

# Classifying iris with three scikit-learn models

A short walkthrough: load iris, scale it, then compare three classifiers.

```bash
pip install scikit-learn==0.24.2
```

Load iris, split it, and scale the features:

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
X, y = load_iris(return_X_y=True)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, random_state=0)
scaler = StandardScaler().fit(X_tr)
print(X.shape, len(X_te), round(float(scaler.mean_[0]), 3))
```

Plain logistic regression with no penalty:

```python
from sklearn.linear_model import LogisticRegression
clf = LogisticRegression(penalty='none', max_iter=1000)
clf.fit(scaler.transform(X_tr), y_tr)
print(round(clf.score(scaler.transform(X_te), y_te), 3))
```

An SGD classifier with log loss, scored with log loss:

```python
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import log_loss
sgd = SGDClassifier(loss='log', max_iter=1000, tol=1e-3, random_state=0)
sgd.fit(scaler.transform(X_tr), y_tr)
proba = sgd.predict_proba(scaler.transform(X_te))
print(round(log_loss(y_te, proba, eps=1e-15), 3))
```

A random forest, with the usual feature subset size:

```python
from sklearn.ensemble import RandomForestClassifier
rf = RandomForestClassifier(n_estimators=50, max_features='auto', random_state=0)
rf.fit(scaler.transform(X_tr), y_tr)
print(round(rf.score(scaler.transform(X_te), y_te), 3))
```

Class counts, which need no change:

```python
import numpy as np
print(np.bincount(y).tolist())
```
