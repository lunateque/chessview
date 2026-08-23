"""
Game domain exceptions.
"""


class IllegalMove(Exception):
    """Raised when python-chess rejects a UCI move as illegal."""
    pass


class NotYourTurn(Exception):
    """Raised when a player tries to move when it's not their turn."""
    pass


class GameNotActive(Exception):
    """Raised when attempting an action on a game that has already ended."""
    pass


class GameNotFound(Exception):
    """Raised when a game lookup returns nothing."""
    pass


class GameAccessDenied(Exception):
    """Raised when a user is not allowed to act on a game."""
    pass


class DrawOfferPending(Exception):
    """Raised when a counter-offer arrives while a draw offer is pending."""
    pass


class DrawOfferNotFound(Exception):
    """Raised when resolving a game without a pending draw offer."""
    pass


class DrawOfferNotAcceptable(Exception):
    """Raised when the offerer tries to resolve their own draw offer."""
    pass


class ConcurrentMoveConflict(Exception):
    """Raised when another move commits the same move number first."""
    pass
