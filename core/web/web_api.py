from .data_api import DataApi
from .forward_api import ForwardApi
from .media_api import MediaApi
from .token_api import TokenApi


class GiftiaWebApi(MediaApi, ForwardApi, DataApi, TokenApi):
    """Giftia plugin web APIs for dashboard pages.

    Facade that aggregates all API domains via multiple inheritance.
    See MediaApi for media-related endpoints, ForwardApi for merged
    forward records, DataApi for chat history, memories, bot status, and
    TokenApi for token statistics.
    """

    pass
