"""Allow ``python -m app`` to start the worker."""

from app.main import main

raise SystemExit(main())
