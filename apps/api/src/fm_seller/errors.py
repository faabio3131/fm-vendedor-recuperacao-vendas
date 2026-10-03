"""Erros da aplicação com código estável para a interface."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def unauthorized(message: str = "Entre com sua conta Google para continuar.") -> AppError:
    return AppError(401, "unauthorized", message)


def forbidden(message: str = "Você não tem permissão para isso.") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(message: str = "Não encontrado.") -> AppError:
    return AppError(404, "not_found", message)


def bad_request(code: str, message: str) -> AppError:
    return AppError(400, code, message)
