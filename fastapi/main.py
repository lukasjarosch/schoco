from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_jwt_auth.exceptions import AuthJWTException
from fastapi.responses import JSONResponse
from multiprocessing import Lock, current_process
import database_config
import users
import code
import cookies_api


app = FastAPI()

app.include_router(users.users)
app.include_router(code.code)


@app.exception_handler(AuthJWTException)
def authjwt_exception_handler(request: Request, exc: AuthJWTException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message}
    )


origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

app.add_middleware(CORSMiddleware, allow_origins=origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


lock = Lock()


@app.on_event("startup")
def startup_event():
    database_config.create_db_and_tables()
    with lock:
        cookies_api.fillNewContainersQueue()
