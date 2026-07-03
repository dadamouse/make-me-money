"""指令處理所需的依賴容器。"""
from dataclasses import dataclass

from .chart import ChartStore
from .pending import PendingChoices
from .supabase import SupabaseClient
from .twse import TwseClient


@dataclass
class Deps:
    db: SupabaseClient
    twse: TwseClient
    pending: PendingChoices
    charts: ChartStore
    base_url: str
