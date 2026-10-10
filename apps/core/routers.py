"""Routing helpers.

`ActionOnlyRouter` exists for viewsets that have no model behind them - the
auth endpoints, sync, and the dashboard aggregates. `DefaultRouter` insists on
generating list/detail routes and those would 500 on a queryset-less viewset,
so these get a router that only wires up `@action` methods.

The payoff is that every URL in the project comes from a router registration
rather than a hand-maintained `path(...)` list, and custom endpoints sit on the
same class as the resource they belong to instead of in a parallel APIView.
"""

from rest_framework.routers import SimpleRouter


class ActionOnlyRouter(SimpleRouter):
    """Generates routes for `@action` methods only - no list/detail."""

    routes = [route for route in SimpleRouter.routes if route.name not in ("list", "detail")]