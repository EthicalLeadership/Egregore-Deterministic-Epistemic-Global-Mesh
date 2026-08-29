from egregore.interface.bootstrap import create_app

app = create_app()

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "egregore.interface.__main__:app",
        host="0.0.0.0",  # noqa: S104
        port=int(os.environ.get('EGREGORE_PORT', '8100')),
    )
