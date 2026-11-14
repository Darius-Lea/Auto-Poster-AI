"""Allow `python -m features.clip_studio ...` as the feature CLI."""
from features.clip_studio.clip_studio import main

if __name__ == "__main__":
    raise SystemExit(main())
