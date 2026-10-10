from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CursorPageNumberPagination(PageNumberPagination):
    """Plain page-number pagination with a stable, explicit envelope.

    Delta pulls do not use this - they use ``SyncState.seq`` - but list
    endpoints do, and an explicit envelope keeps the Flutter client simple.
    """

    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "num_pages": self.page.paginator.num_pages,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )
