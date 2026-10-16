"""Typed request access to services created by the application lifespan."""

from typing import Annotated

from fastapi import Depends, Request

from app.web.runtime import WebServices


def get_services(request: Request) -> WebServices:
    return request.app.state.services


Services = Annotated[WebServices, Depends(get_services)]
