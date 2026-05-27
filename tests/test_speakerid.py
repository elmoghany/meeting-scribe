from app.pipeline.speakerid import cosine, match, mean_embedding, running_mean


def test_cosine():
    assert cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert abs(cosine([1, 0], [0, 1])) < 1e-9
    assert cosine([], [1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0


def test_mean_embedding():
    assert mean_embedding([[2.0, 0.0], [0.0, 2.0]]) == [1.0, 1.0]
    assert mean_embedding([]) == []
    assert mean_embedding([[1.0, 2.0]]) == [1.0, 2.0]


def test_match_picks_best_above_threshold():
    profiles = [{"name": "A", "embedding": [1, 0, 0]},
                {"name": "B", "embedding": [0, 1, 0]}]
    m = match([0.95, 0.05, 0.0], profiles, threshold=0.75)
    assert m and m[0] == "A" and m[1] > 0.9


def test_match_returns_none_below_threshold():
    profiles = [{"name": "A", "embedding": [1, 0, 0]}, {"name": "B", "embedding": [0, 1, 0]}]
    assert match([0.3, 0.3, 0.9], profiles, threshold=0.75) is None
    assert match([1, 0, 0], [], threshold=0.75) is None


def test_running_mean():
    assert running_mean([2.0, 2.0], 1, [4.0, 4.0]) == [3.0, 3.0]  # (2*1+4)/2
    assert running_mean([], 0, [1.0, 2.0]) == [1.0, 2.0]
    assert running_mean([1.0], 1, []) == [1.0]  # bad new sample -> unchanged
