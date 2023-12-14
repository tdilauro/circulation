from flask import Flask
from sqlalchemy.orm import Session

from api.circulation_manager import CirculationManager


class PalaceFlask(Flask):
    """
    A subclass of Flask sets properties used by Palace.

                       ╒▓▓@=:===:╗φ
                        ,██Ü_╠░_█▌_
             ___     ─-'██▌  ▌H ▐█▓
      ______░╓___________▐█▌▄╬▄▄█____________________
     ▐██▒         `````░░░░░Ü░░░░╠╠╠╠╬╬╬╬╬╬╬╬╠▒▒Ü` ▐▓L
     ▐██▌              »»»░░░░░░░▒▒╠╠╠╬╬╬╬╬╬╠▒▒ÜÜ  ╚▓▒
     ▐██▌              `»│»»»░░░░▒▒▒╠╠╬╬╬╬╬╬╠▒▒░░  ╚▓▌
     ▐██▌              »»»»»░░░░░▒▒▒╠╠╬╬╬╬╬╬╠▒ÜÜ░  ▐▓▌
     ▐██▌             `»»»»░░░░░░▒▒▒╠╠╬╬╬╬╬╬▒▒Ü░░  ▐▓▌
     ▐█▓▌              `»»»»░░░░░▒▒▒╠╠╠╬╬╬╬╬▒▒Ü░░  ╚▓▌
     ▐█▓▌              »»»»░░░░░░▒▒▒╠╠╠╠╬╬╬╠Ü▒░░░  ╠▓▌
     ▐█▓▌              »»»»░░░░░░▒▒▒▒╠╠╠╬╬╬╠▒Ü░░░  ╠╣▌
     ▐██▌                               ╬╬╬╠▒▒░░░  ╠╣▌
     ▐██▌           The Palace Project       ▒░░░  ╠╣▌
     ▐██▌                               ╠╬╠╠▒▒░░░  ║╣▌
     ▐██▌              »»»»░░░░░░▒▒▒▒▒╠╠╠╠╠╠▒▒░░░  ║▓▌
     ▐██▌             `»»»░░░░░░░░▒▒▒▒╠╠╠╠╠▒▒▒░░░  ╠╣▌
     ▐██▌             »»»»░░░░░░░░▒Ü▒▒╠╠╠╠╠▒▒Ü░░░  ▐╫▌
     ▐██▌             `»»»░░░░░░░░▒▒▒▒╠╠╠╠╠▒▒Ü░░░  ▐╣▌
      ██╬░     `»»»``»»»»░░░░░░░░░░▒▒▒▒▒▒▒▒▒▒Ü░░░░ [╣▌
      █▓╬░     `»` »`»»»»»░░░░░░░░░░▒▒▒▒▒▒▒▒▒Ü░░░░ [╫▌
      ▓▓╬░      `   `»»»»»»░░░░░░░Ü▒▒▒▒▒▒▒▒▒▒Ü░░░░ [╫▌
      ▓▓╬░»``  » `  » »»»»░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒Ü░░░░ [╫▌
      ▓▓╬░»_` ``»`»»»»»»»»»░░░░░░Ü▒▒▒▒▒▒▒▒▒▒▒▒Ü░░░ |╫▌
      ▓▓╬░»»»»»»»»»»»»»»»»░░░░░░░▒░▒▒▒▒▒▒▒▒▒▒ÜÜ░Ü░_|╟▌
      ╣▓╬░»»»»»»»»»»»»»»»»»░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒ÜÜ░⌐|╟▌
      ╝▓╬░__»»»»»»»»»»░»░░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒░░░_|╠╛
      ╚╚╚╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╙╚╚╚╚╚╚╚ÜÜÜÜÜÜÜÜÜÜÜÜÜ╙╚ÜH

    Palace: You're going to need a stiff drink after this.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._db: Session
        self.manager: CirculationManager
