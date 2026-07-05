from .data_api import DataApi
from .forward_api import ForwardApi
from .media_api import MediaApi


class GiftiaWebApi(MediaApi, ForwardApi, DataApi):
    """Giftia plugin web APIs for dashboard pages.

    Facade that aggregates all API domains via multiple inheritance.
    See MediaApi for media-related endpoints, ForwardApi for merged
    forward records, and DataApi for chat history, memories, bot status,
    and profile endpoints.
    """

    pass
