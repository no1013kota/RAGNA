"""RAGNA Botのデータアクセス公開API。

既存コードとの互換性のため各領域の関数を再公開します。新しいコードでは
``database.coin`` や ``database.hotel`` など、担当領域からimportしてください。

再公開だけを行うファイルなので、未使用import・ワイルドカードimportの指摘は
意図どおりです。
"""

# ruff: noqa: F401,F403

from .coin import *
from .connection import *
from .hotel import *
from .member import *
from .ranking import *
from .ticket import *
from .trial_member import *
from .xp import *
