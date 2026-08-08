from egregore.interface.bootstrap import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "egregore.interface.__main__:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
    )
