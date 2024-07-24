from __future__ import annotations

from flask_babel import lazy_gettext as _
from pydantic import AnyHttpUrl, HttpUrl, NonNegativeInt, PositiveInt

from palace.manager.api.circulation import BaseCirculationEbookLoanSettings
from palace.manager.api.lcp.hash import HashingAlgorithm
from palace.manager.core.opds2_import import OPDS2ImporterSettings
from palace.manager.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from palace.manager.sqlalchemy.model.collection import Collection


class ODL2Settings(OPDS2ImporterSettings):
    encryption_algorithm: HashingAlgorithm = FormField(
        default=HashingAlgorithm.SHA256,
        form=ConfigurationFormItem(
            label=_("Passphrase encryption algorithm"),
            description=_("Algorithm used for encrypting the passphrase."),
            type=ConfigurationFormItemType.SELECT,
            required=False,
            options={alg: alg.name for alg in HashingAlgorithm},
        ),
    )
    passphrase_hint_url: HttpUrl = FormField(
        default="https://lyrasis.zendesk.com/",
        form=ConfigurationFormItem(
            label=_("Passphrase hint URL"),
            description=_(
                "Hint URL available to the user when opening an LCP protected publication."
            ),
            type=ConfigurationFormItemType.TEXT,
            required=True,
        ),
    )
    passphrase_hint: str = FormField(
        default="View the help page for more information.",
        form=ConfigurationFormItem(
            label=_("Passphrase hint"),
            description=_(
                "Hint displayed to the user when opening an LCP protected publication."
            ),
            type=ConfigurationFormItemType.TEXT,
            required=True,
        ),
    )
    default_reservation_period: PositiveInt | None = FormField(
        default=Collection.STANDARD_DEFAULT_RESERVATION_PERIOD,
        form=ConfigurationFormItem(
            label=_("Default Reservation Period (in Days)"),
            description=_(
                "The number of days a patron has to check out a book after a hold becomes available."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )
    password: str = FormField(
        form=ConfigurationFormItem(
            label=_("Library's API password"),
            required=True,
        ),
    )
    username: str = FormField(
        form=ConfigurationFormItem(
            label=_("Library's API username"),
            required=True,
        )
    )
    external_account_id: AnyHttpUrl = FormField(
        form=ConfigurationFormItem(
            label=_("ODL feed URL"),
            required=True,
        ),
    )
    skipped_license_formats: list[str] = FormField(
        default=["text/html"],
        alias="odl2_skipped_license_formats",
        form=ConfigurationFormItem(
            label=_("Skipped license formats"),
            description=_(
                "List of license formats that will NOT be imported into Circulation Manager."
            ),
            type=ConfigurationFormItemType.LIST,
            required=False,
        ),
    )

    loan_limit: PositiveInt | None = FormField(
        default=None,
        alias="odl2_loan_limit",
        form=ConfigurationFormItem(
            label=_("Loan limit per patron"),
            description=_(
                "The maximum number of books a patron can have loaned out at any given time."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )

    hold_limit: NonNegativeInt | None = FormField(
        default=None,
        alias="odl2_hold_limit",
        form=ConfigurationFormItem(
            label=_("Hold limit per patron"),
            description=_(
                "The maximum number of books from this collection that a patron can "
                "have on hold at any given time. "
                "<br>A value of 0 means that holds are NOT permitted."
                "<br>No value means that no limit is imposed by this setting."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )


class ODL2LibrarySettings(BaseCirculationEbookLoanSettings):
    pass
