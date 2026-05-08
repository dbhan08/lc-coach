from lc_coach.mastery import infer_patterns_from_slug


def test_two_sum_is_hashing():
    assert "hashing" in infer_patterns_from_slug("two-sum")


def test_lru_cache_is_design():
    assert "design" in infer_patterns_from_slug("lru-cache")


def test_course_schedule_is_topological_sort():
    assert "topological-sort" in infer_patterns_from_slug("course-schedule")
    assert "topological-sort" in infer_patterns_from_slug("course-schedule-ii")


def test_redundant_connection_is_union_find():
    assert "union-find" in infer_patterns_from_slug("redundant-connection")


def test_number_of_islands_is_bfs_dfs():
    assert "bfs-dfs" in infer_patterns_from_slug("number-of-islands")


def test_trapping_rain_water_is_monotonic_stack():
    assert "monotonic-stack" in infer_patterns_from_slug("trapping-rain-water")


def test_kth_largest_is_heap():
    assert "heap" in infer_patterns_from_slug("kth-largest-element-in-an-array")


def test_binary_search_self():
    assert "binary-search" in infer_patterns_from_slug("binary-search")


def test_lowest_common_ancestor_is_graph():
    assert "graph" in infer_patterns_from_slug(
        "lowest-common-ancestor-of-a-binary-tree"
    )


def test_minimum_path_sum_is_dp():
    assert "dp" in infer_patterns_from_slug("minimum-path-sum")


def test_unknown_slug_returns_empty():
    assert infer_patterns_from_slug("brand-new-problem-no-keywords") == []


def test_empty_slug_safe():
    assert infer_patterns_from_slug("") == []
    assert infer_patterns_from_slug(None) == []
