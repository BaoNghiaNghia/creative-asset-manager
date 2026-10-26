"""Small grayscale texture swatches sampled from user-provided embroidery photo.

Photographed cotton-twill background and black satin thread provide micro-
surface characteristics, NOT letter shapes or fabric identity for downstream
caps. The 64px samples are embedded to keep releases offline and reproducible.
"""
from __future__ import annotations
import base64
from io import BytesIO
from PIL import Image

FABRIC_JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAwICQsJCAwLCgsODQwOEh4UEhEREiUbHBYeLCcuLisnKyoxN0Y7MTRCNCorPVM+QkhKTk9OLztWXFVMW0ZNTkv/wAALCABAAEABAREA/8QAGQABAQEAAwAAAAAAAAAAAAAABQQDAAIG/8QALBAAAgEDAwMDAwQDAAAAAAAAAQIDAAQRBRIhEyIxMkFRFEJxI1JhgWKRof/aAAgBAQAAPwD3k0m+CQr8Y4oWVZ47tVUnsXn8U3Gd6cftzQ99a3AeNAOT3GkrFi0SjOSDzUV4LmJixH6cr4Wt9PfsK/cpwaw1FpWMrEZjJ2Lj5q3T+LYE+Sc1JfRuyyzA4VzgD+Ko0h2aE7vK4Fd71JHaSZG4RcVJom4K6vwScgGq75JZTDHGuQuWJ+KM0rqLduJM9zUpcBsqka5BJP4qXTJmkVt3jyK3vVkeOIIM5O41PYSlpW2kbSe6rLt2FthB6jg0WrSx34+0Y20wsvbJgelf90RN143hUptI7qYhbchP+NFRXHTmWMptG3FJLMOi+B6FoNLn6WWPAwD5p+Ah4kbHnmiLiUwmZwuWZ/P8VbaS71yx9QAqfU5mjlkIXlR59q20676sAYjkDH5rC8mUyyEjnOM1RZHejfBwKm1GJHnYEY2JxWmkXRkjCH7RW2pGOOOEEAAnn+ahsblWumWMdmeKR1F4orQmUcO2PyaN06aP6lo19LntFV34QmMN6PJx71nps4M7Rqp2E+9W3/S+mkJHexwDR0UqpcgKAMjH90q8cbxM0yghVJ5FCrJDaCEKvOck00BFNEhkUMoywzQf6Fl05F9TNn+qXeFBEzsMhF8USlwsM6Op4Y/8piMLOkbSc++KMuuj1ppMjcjdoFIW04uYC3sVxU2qQQdRDgKqLziqLNxNbgfaVxWerW9uwiUAKEHn4rZJBJGw9mwMVFd2cBmAU7Qq5P5q2zZXjQg8AYqe+0+PZHt4Ltlz81yzRYFeJH3DcBW9/ao0ckrk5OAo+Km0/bErRE5yc5qm5hUrJJIMhVwB7V//2Q=="
THREAD_JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAwICQsJCAwLCgsODQwOEh4UEhEREiUbHBYeLCcuLisnKyoxN0Y7MTRCNCorPVM+QkhKTk9OLztWXFVMW0ZNTkv/wAALCABAAEABAREA/8QAGQABAQADAQAAAAAAAAAAAAAAAgMAAQQG/8QAKxAAAgEEAQMDAwQDAAAAAAAAAQIDAAQREkEhMVETImEUcYEFM0JDUpGh/9oACAEBAAA/APAx3VwzlPTwT3DVUSTRnL2xx/l4rA8zHZMjkanvWpbu5fAaDLDkc1VAzxhzJ6bHjxUpp9mG8mzDkCgLkkFSgHzii04QYZMg84q6JgHNyH/FJ45pI8xzKVHXBHeuc2+x2L6nytY9u4AMV3saUcdy7HeYa47gUfpVVsyTYU1iI4YlJhqOaBMvUCRXHgikZoTl8hMnGKw/qGi+mse47Aigl4wYZiIHNZJdBzssYyOFFbF1kAGNwD45qwilfokLN+O1CSFojgxMPOKnvETrvqfkdq7Rao4w1upHnNBFe3kKRYjHfqM0kkebbeeIY6YK96KyNH7RqV+K55Z7p3HtUgdulU+sv9dAMDHUCueSe4QjZWX71gk9T3NFkecUi104GV1A8GqLZM42N4qseuDQazkc6vMpHFF4PSwGuD/qqQqBgtcjXxzSeONjkSNgcg1oZdMpMr/Dd63ECYpIpGAB6qM9jVY5YU6ykOOdTU7pbOTDrHKPxU97VdQiSgA881e5dGjBS2PTmueBoySBb4Y9iavIBH/XnPC1E+nt+0VaixDD24BrqNsowzBI/OpoRvKSVikBTP8AIVuX1lbJcEj4oB5HbJlOfFB9wfcxB4NbEcgUuJwBWI4dcOQfzUpAdTlBr/2uwSwMvsiZhwQKS2xnjOiFF5btijJZCJNhcbVIRxOuCTnyKcdmoHWY6/NaxGRjYgfeptbwOMjYY5FRkFtggSPtwa//2Q=="


def reference_patch(kind: str) -> Image.Image:
    raw = FABRIC_JPEG if kind == "fabric" else THREAD_JPEG if kind == "thread" else None
    if raw is None:
        raise ValueError("Unknown fabric reference patch")
    with Image.open(BytesIO(base64.b64decode(raw, validate=True))) as image:
        image.load()
        if image.size != (64, 64):
            raise ValueError("Invalid user reference patch")
        return image.convert("L")
