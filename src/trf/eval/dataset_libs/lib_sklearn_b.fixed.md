---
title: Predicting diabetes progression with linear models
date: 2020-11-04
---

# Predicting diabetes progression with linear models

A short walkthrough: load the diabetes data, fit a linear model, then encode a category and try a tree.

```bash
pip install scikit-learn==1.9.1
```

Load diabetes and split it:

```python
from sklearn.datasets import load_diabetes
from sklearn.model_selection import train_test_split
X, y = load_diabetes(return_X_y=True)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=0)
print(X.shape, len(y_te))
```

Ordinary least squares, with the intercept and feature normalisation:

```python
from sklearn.linear_model import LinearRegression
lr = LinearRegression().fit(X_tr, y_tr)
print(round(lr.score(X_te, y_te), 3))
```

One-hot encode a small categorical column, dense output:

```python
from sklearn.preprocessing import OneHotEncoder
colors = [["red"], ["blue"], ["red"], ["green"]]
enc = OneHotEncoder(sparse_output=False).fit(colors)
print(enc.transform(colors).sum(axis=0).tolist())
```

Name the encoded columns:

```python
from sklearn.preprocessing import OneHotEncoder
names = OneHotEncoder().fit(colors).get_feature_names_out()
print(list(names))
```

A shallow regression tree for comparison:

```python
from sklearn.tree import DecisionTreeRegressor
tree = DecisionTreeRegressor(criterion='squared_error', max_depth=3, random_state=0)
tree.fit(X_tr, y_tr)
print(round(tree.score(X_te, y_te), 3))
```

Mean training target, computed with numpy:

```python
import numpy as np
print(round(float(np.mean(y_tr)), 3))
```
