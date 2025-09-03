#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable, Generator, Iterable, Awaitable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import asyncssh
from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

from palace.manager.integration.patron_auth.sip2.dialect import Dialect
from palace.manager.integration.patron_auth.sip2.provider import (
    SIP2AuthenticationProvider,
    SIP2LibrarySettings,
    SIP2Settings,
)
from palace.manager.integration.settings import SettingsValidationError

# from palace.manager.util.ssh_tunnel_async import SSHTunnel

logging.basicConfig(level=logging.WARNING)

FILENAME = "/Users/Shared/src/tpp/circulation/sip2.integrations.all.jsonl"
CONCURRECY = 10
SIP_TIMEOUT_SECONDS = 10
DEFAULT_DIALECT = Dialect.GENERIC_ILS
SSH_HOST = "tpp-prod-web-epsilon-2.lyrtech.org"
SSH_PORT = 922


class IntegrationConfiguration(BaseModel):
    cm: str
    integration_name: str
    # dialect: str | None = Field(DEFAULT_DIALECT.value, description="The ILS dialect used for this integration.")
    library_name: str = Field("(none)", description="The library that is tested against.")
    passed_st: bool | None = Field(..., description="Full pass of all tests.")
    settings: SIP2Settings
    lib_settings: SIP2LibrarySettings

    model_config = ConfigDict(extra="allow")


def warn(*args, **kwargs):
    """Print a warning to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def integration_configurations_from_dict(config_dicts: Iterable[dict[str, Any]]) -> Generator[IntegrationConfiguration]:
    """Yield IntegrationConfiguration objects from JSON configuration dictionaries."""
    for config in config_dicts:
        try:
            yield IntegrationConfiguration.model_validate(config)
        except SettingsValidationError as e:
            warn(f"Error with configuration: {config}: {e}\n{e.errors()}")
            raise
        except ValidationError as e:
            warn(f"Error with configuration: {config}: {e}\n{e.errors()}")
            raise
        except (AttributeError) as e:
            warn(f"Error with configuration: {config}: {e}")
            raise


def get_configuration(filename: str) -> str:
    """Read a single JSON configuration line from a file."""
    with open(filename) as f:
        for line in f:
            yield line.strip()

def get_configuration_dict(json_lines: Iterable[str]) -> Generator[dict[str, Any]]:
    for json_string in json_lines:
        yield json.loads(json_string)


def test_config_for(config: IntegrationConfiguration, **overridden_settings) -> IntegrationConfiguration:
    config_dict = config.model_dump()
    new_settings = config_dict["settings"] | overridden_settings
    return IntegrationConfiguration.model_validate(config_dict | {"settings": new_settings})


def with_retry(
    max_attempts: int = 3,
    retry_delay: float = 5
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_attempts - 1:
                        warn(f"Attempt {attempt + 1} failed: {e}. Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                    else:
                        raise
        return wrapper
    return decorator


@with_retry()
async def connect_with_retry(*args, **kwargs):
    return await asyncssh.connect(*args, **kwargs)


async def run_self_test(config: IntegrationConfiguration) -> None:
    warn(
        f"Running self-test for '{config.integration_name}' using library '{config.library_name}' "
        f"at {config.settings.url}:{config.settings.port}..."
    )

    local_host = "127.0.0.1"

    async with (
        await connect_with_retry(
            host=SSH_HOST,
            port=SSH_PORT,
            agent_forwarding=True,
        ) as connection,
        connection.forward_local_port(
            local_host, 0, config.settings.url, config.settings.port
        ) as listener,
    ):
        local_port = listener.get_port()

        warn(
            f"> Connected to SIP2 server at {config.settings.url}:{config.settings.port} via {local_host}:{local_port} "
            f" for '{config.integration_name}' using library '{config.library_name}'"
        )

        original_results_summary = [
            {k: v for k, v in result.items() if k in ("name", "success")}
            for result in config.self_test_results["results"]
        ]

        # This is the configuration that we'll actually test.
        test_config = test_config_for(config, ils=Dialect.SIP_V2, url=local_host, port=local_port)

        test_provider = SIP2AuthenticationProvider(
            library_id=0,
            integration_id=0,
            settings=test_config.settings,
            library_settings=test_config.lib_settings,
        )
        test_provider.timeout = SIP_TIMEOUT_SECONDS

        # Run the tests.
        # This is a sync function, so it needs to be run in a thread to access the listener.
        test_results = await asyncio.to_thread(sip_test, test_provider)

        result_json = {
            k: v for k, v in config.model_dump().items() if k in ["cm", "integration_name", "library_name", "passed_st"]
        } | {
            "ils_dialect": config.settings.ils.value,
            "v2_test_results": test_results,
            "original_results_summary": original_results_summary,
        }
        print(json.dumps(result_json))


T = TypeVar("T")
P = ParamSpec("P")


def run_test(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> tuple[T | Exception, bool]:
    try:
        return_value = func(*args, **kwargs)
        success = True
    except Exception as e:
        return_value = e
        success = False

    return return_value, success


def sip_test(provider: SIP2AuthenticationProvider) -> dict[str, bool]:
    # Start with an assumption that everything is going to work.
    overall_success = True
    sip = provider.client
    # warn(f"> Provider server: {provider.server}:{provider.port} -> {sip.target_server}:{sip.target_port}")

    # Self-test steps:
    # - Make a SIP connection
    _, connect_success = run_test(sip.connect)
    overall_success = overall_success and connect_success

    # - If there are server login credentials, then log in.
    if (login_creds := provider.login_user_id is not None):
        login_info, login_success = run_test(sip.login)
        # warn(
        #     f"> Test Login with username '{provider.login_user_id}' and password '{provider.login_password}' "
        #     f"=> {login_success=} => {login_info=}"
        # )
    else:
        login_success = True
    overall_success = overall_success and login_success

    # - Get remote server status.
    status_info, status_success = run_test(sip.sc_status)
    overall_success = overall_success and status_success
    # warn(
    #     f"> Test ILS SIP Service Info (SC Status) => {status_success=} "
    #     f"=> {json.dumps(status_info) if status_success else status_info}"
    # )

    # if status_success:
    #     warn(f"Remote server status: {json.dumps(status_info, indent=2)}")

    # - If login was successful and there are test user credentials, then:
    #   - Test patron information
    patron_info_success = False
    if (patron_creds := provider.test_username is not None) and login_success:
        patron_info, patron_info_success = run_test(sip.patron_information, provider.test_username, provider.test_password)
        # warn(
        #     f"> Test patron info request for {provider.test_username} / {provider.test_password} "
        #     f"=> {patron_info_success=} => {json.dumps(patron_info) if patron_info_success else patron_info}"
        # )

        end_response, _ = run_test(sip.end_session, provider.test_username, provider.test_password)

    overall_success = overall_success and patron_info_success

    _, _ = run_test(sip.disconnect)

    return {
        "overall": overall_success,
        "connect": connect_success,
        "login": login_success,
        "sc_status": status_success,
        "patron": patron_info_success,
        "login_creds": login_creds,
        "patron_creds": patron_creds,
    }


async def run_with_semaphore(
    semaphore: asyncio.Semaphore,
    func: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs
) -> T:
    async with semaphore:
        return await func(*args, **kwargs)


def filter_unactionable(config_dicts: Iterable[dict[str, Any]]) -> Generator[dict[str, Any]]:
    for config in config_dicts:
        if config.get("lib_settings") is None:
            warn(f"> Skipping {config['cm']} {config['integration_name']}: `lib_settings` is missing.")
            continue
        yield config


async def main():
    semaphore = asyncio.Semaphore(CONCURRECY)

    json_lines = get_configuration(FILENAME)
    extracted_config_dicts = get_configuration_dict(json_lines)
    filtered_config_dicts = filter_unactionable(extracted_config_dicts)
    integrations_from_dicts = integration_configurations_from_dict(filtered_config_dicts)

    tasks = [
        run_with_semaphore(semaphore, run_self_test, integration)
        for integration in integrations_from_dicts
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    warn(f"*** Running SIP self-test script with file '{FILENAME}' ***")
    asyncio.run(main())
    warn("*** Done! ***")
