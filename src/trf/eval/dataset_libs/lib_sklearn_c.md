---
title: Grouping points with KMeans and agglomerative clustering
date: 2020-08-12
---

# Grouping points with KMeans and agglomerative clustering

A short walkthrough: make blobs, cluster them two ways, then score a tiny split.

```bash
pip install scikit-learn==0.22.2.post1
```

Make a small blob dataset:

```python
import numpy as np
from sklearn.datasets import make_blobs
X, true = make_blobs(n_samples=30, centers=3, n_features=2, random_state=0)
print(X.shape, sorted(set(true.tolist())))
```

Validate the matrix before clustering:

```python
from sklearn.utils.validation import check_array
Xc = check_array(X, force_all_finite=True)
print(Xc.shape)
```

KMeans with three clusters:

```python
from sklearn.cluster import KMeans
km = KMeans(n_clusters=3, n_init=10, n_jobs=1, precompute_distances=True, random_state=0)
labels = km.fit_predict(X)
print(sorted(np.bincount(labels).tolist()))
```

Agglomerative clustering using Euclidean distance:

```python
from sklearn.cluster import AgglomerativeClustering
agg = AgglomerativeClustering(n_clusters=3, affinity='euclidean', linkage='average')
labels2 = agg.fit_predict(X)
print(sorted(np.bincount(labels2).tolist()))
```

Silhouette score on a tiny hand-made split:

```python
from sklearn.metrics import silhouette_score
pts = np.array([[0.0], [0.1], [5.0], [5.1]])
print(round(float(silhouette_score(pts, [0, 0, 1, 1])), 3))
```
