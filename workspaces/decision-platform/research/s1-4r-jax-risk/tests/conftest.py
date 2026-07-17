"""Deterministic Hypothesis profile for local, CI, and OCI correctness."""

from hypothesis import settings

# Random entropy나 machine speed가 correctness 결과를 바꾸지 않게 profile을 고정한다.
settings.register_profile(
    "s1_4r_deterministic",
    deadline=None,
    derandomize=True,
    max_examples=100,
    print_blob=True,
)
settings.load_profile("s1_4r_deterministic")
