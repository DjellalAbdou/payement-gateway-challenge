import uvicorn


def main():
    uvicorn.run(
        app="payment_gateway_api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=1,
    )


if __name__ == "__main__":
    main()
