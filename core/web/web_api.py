from .data_api import DataApi
from .media_api import MediaApi


class GiftiaWebApi(MediaApi, DataApi):
    """Giftia plugin web APIs for dashboard pages.

    Facade that aggregates all API domains via multiple inheritance.
    See MediaApi for media-related endpoints and DataApi for
    chat history, memories, bot status, and profile endpoints.
    """

    pass
