# SPDX-FileCopyrightText: 2024 Jeff Epler for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""Routines for performing image manipulation"""

import struct

import ulab.numpy as np


def _bytes_per_row(source_width: int) -> int:
    """Internal function to determine bitmap bytes per row"""
    pixel_bytes = 3 * source_width
    padding_bytes = (4 - (pixel_bytes % 4)) % 4
    return pixel_bytes + padding_bytes


def _write_bmp_header(output_file, filesize):
    """Internal function to write bitmap header"""
    output_file.write(bytes("BM", "ascii"))
    output_file.write(struct.pack("<I", filesize))
    output_file.write(b"\00\x00")
    output_file.write(b"\00\x00")
    output_file.write(struct.pack("<I", 54))


def _write_dib_header(output_file, width: int, height: int) -> None:
    """Internal function to write bitmap "dib" header"""
    output_file.write(struct.pack("<I", 40))
    output_file.write(struct.pack("<I", width))
    output_file.write(struct.pack("<I", height))
    output_file.write(struct.pack("<H", 1))
    output_file.write(struct.pack("<H", 24))
    for _ in range(24):
        output_file.write(b"\x00")


def components_to_bitmap(output_file, r, g, b):
    """Write image components to an uncompressed 24-bit .bmp format file"""
    height, width = r.shape
    pixel_bytes = 3 * width
    padding_bytes = (4 - (pixel_bytes % 4)) % 4
    filesize = 54 + height * (pixel_bytes + padding_bytes)
    _write_bmp_header(output_file, filesize)
    _write_dib_header(output_file, width, height)
    pad = b"\0" * padding_bytes
    view = memoryview(buffer_from_components_rgb888(r, g, b))
    # Write out image data in reverse order with padding between rows
    for i in range(0, len(view), pixel_bytes)[::-1]:
        output_file.write(view[i : i + pixel_bytes])
        output_file.write(pad)


def _np_convolve_same(arr, coeffs):
    """Internal function to perform the np.convolve(arr, coeffs, mode="same") operation

    This is not directly supported on ulab, so we have to slice the "full" mode result
    """
    if len(arr) < len(coeffs):
        arr, coeffs = coeffs, arr
    tmp = np.convolve(arr, coeffs)
    n = len(arr)
    offset = (len(coeffs) - 1) // 2
    result = tmp[offset : offset + n]
    return result


FIVE_BITS = 0b11111
SIX_BITS = 0b111111
EIGHT_BITS = 0b11111111


def bitmap_as_array(bitmap):
    """Create an array object that accesses the bitmap data"""
    if bitmap.width % 2:
        raise ValueError("Can only work on even-width bitmaps")
    return np.frombuffer(bitmap, dtype=np.uint16).reshape((bitmap.height, bitmap.width))


def array_cast(arr, dtype):
    """Cast an array to a given type and shape. The new type must match the original
    type's size in bytes."""
    return np.frombuffer(arr, dtype=dtype).reshape(arr.shape)


def bitmap_to_components_rgb565(bitmap):
    """Convert a RGB65_BYTESWAPPED image to int16 components in the [0,255] inclusive range

    This requires higher memory than uint8, but allows more arithmetic on pixel values;
    converting back to bitmap clamps values to the appropriate range."""
    arr = bitmap_as_array(bitmap)
    arr.byteswap(inplace=True)
    r = array_cast(np.right_shift(arr, 8) & 0xF8, np.int16)
    g = array_cast(np.right_shift(arr, 3) & 0xFC, np.int16)
    b = array_cast(np.left_shift(arr, 3) & 0xF8, np.int16)
    arr.byteswap(inplace=True)
    return r, g, b


def bitmap_from_components_inplace_rgb565(
    bitmap, r, g, b
):  # pylint: disable=invalid-name
    """Update a bitmap in-place with new RGB values"""
    dest = bitmap_as_array(bitmap)
    r = array_cast(np.maximum(np.minimum(r, 255), 0), np.uint16)
    g = array_cast(np.maximum(np.minimum(g, 255), 0), np.uint16)
    b = array_cast(np.maximum(np.minimum(b, 255), 0), np.uint16)
    dest[:] = np.left_shift(r & 0xF8, 8)
    dest[:] |= np.left_shift(g & 0xFC, 3)
    dest[:] |= np.right_shift(b, 3)
    dest.byteswap(inplace=True)
    return bitmap


def as_flat(arr):
    """Flatten an array, ensuring no copy is made"""
    return np.frombuffer(arr, arr.dtype)


def buffer_from_components_rgb888(r, g, b):
    """Convert the individual color components to a single RGB888 buffer in memory"""
    r = as_flat(r)
    g = as_flat(g)
    b = as_flat(b)
    r = np.maximum(np.minimum(r, 0x3F), 0)
    g = np.maximum(np.minimum(g, 0x3F), 0)
    b = np.maximum(np.minimum(b, 0x3F), 0)
    result = np.zeros(3 * len(r), dtype=np.uint8)
    result[2::3] = r
    result[1::3] = g
    result[0::3] = b
    return result


def symmetric_filter_inplace(data, coeffs, scale):
    """Apply a symmetric separable filter to a 2d array, changing it in place.

    The same filter is applied to image rows and image columns. This is appropriate for
    many common kinds of image filters such as blur, sharpen, and edge detect.

    Normally, scale is sum(coeffs)."""
    # First run the filter across each row
    n_rows = data.shape[0]
    for i in range(n_rows):
        data[i, :] = _np_convolve_same(data[i, :], coeffs) // scale

    # Run the filter across each column
    n_cols = data.shape[1]
    for i in range(n_cols):
        data[:, i] = _np_convolve_same(data[:, i], coeffs) // scale

    return data


def bitmap_symmetric_filter_inplace(bitmap, coeffs, scale):
    """Apply a symmetric filter to an image, updating the original image"""
    r, g, b = bitmap_to_components_rgb565(bitmap)
    symmetric_filter_inplace(r, coeffs, scale)
    symmetric_filter_inplace(g, coeffs, scale)
    symmetric_filter_inplace(b, coeffs, scale)
    return bitmap_from_components_inplace_rgb565(bitmap, r, g, b)


def bitmap_channel_filter3_inplace(
    bitmap, r_func=lambda r, g, b: r, g_func=lambda r, g, b: g, b_func=lambda r, g, b: b
):
    """Perform channel filtering in place, updating the original image

    Each callback function recieves all 3 channels"""
    r, g, b = bitmap_to_components_rgb565(bitmap)
    r = r_func(r, g, b)
    g = g_func(r, g, b)
    b = b_func(r, g, b)
    return bitmap_from_components_inplace_rgb565(bitmap, r, g, b)


def bitmap_channel_filter1_inplace(
    bitmap, r_func=lambda r: r, g_func=lambda g: g, b_func=lambda b: b
):
    """Perform channel filtering in place, updating the original image

    Each callback function recieves just its own channel data."""
    r, g, b = bitmap_to_components_rgb565(bitmap)
    r[:] = r_func(r)
    g[:] = g_func(g)
    b[:] = b_func(b)
    return bitmap_from_components_inplace_rgb565(bitmap, r, g, b)


def solarize_channel(data, threshold=128):
    """Solarize an image channel.

    If the channel value is above a threshold, it is inverted. Otherwise, it is unchanged.
    """
    return (255 - data) * (data > threshold) + data * (data <= threshold)


def solarize(bitmap, threshold=128):
    """Apply a per-channel solarize filter to an image in place"""

    def do_solarize(channel):
        return solarize_channel(channel, threshold)

    return bitmap_channel_filter1_inplace(bitmap, do_solarize, do_solarize, do_solarize)


def sepia(bitmap):
    """Apply a sepia filter to an image in place

    based on some coefficients I found on the internet"""
    return bitmap_channel_filter3_inplace(
        bitmap,
        lambda r, g, b: np.right_shift(50 * r + 98 * g + 24 * b, 7),
        lambda r, g, b: np.right_shift(44 * r + 88 * g + 42 * b, 7),
        lambda r, g, b: np.right_shift(35 * r + 69 * g + 17 * b, 7),
    )


def greyscale(bitmap):
    """Convert an image to greyscale"""
    r, g, b = bitmap_to_components_rgb565(bitmap)
    luminance = np.right_shift(38 * r + 75 * g + 15 * b, 7)
    return bitmap_from_components_inplace_rgb565(
        bitmap, luminance, luminance, luminance
    )


def _identity(channel):
    """An internal function to return a channel unchanged"""
    return channel


def _half(channel):
    """An internal function to divide channel values by two"""
    return channel // 2


def red_cast(bitmap):
    """Give an image a red cast by dividing G and B channels in half"""
    return bitmap_channel_filter1_inplace(bitmap, _identity, _half, _half)


def green_cast(bitmap):
    """Give an image a green cast by dividing R and B channels in half"""
    return bitmap_channel_filter1_inplace(bitmap, _half, _identity, _half)


def blue_cast(bitmap):
    """Give an image a blue cast by dividing R and G channels in half"""
    return bitmap_channel_filter1_inplace(bitmap, _half, _half, _identity)


def blur(bitmap):
    """Blur a bitmap"""
    return bitmap_symmetric_filter_inplace(bitmap, np.array([1, 2, 1]), scale=4)


def sharpen(bitmap):
    """Sharpen a bitmap"""
    return bitmap_symmetric_filter_inplace(
        bitmap, np.array([-1, -1, 9, -1, -1]), scale=5
    )


def edgedetect(bitmap):
    """Run an edge detection routine on a bitmap"""
    coefficients = np.array([-1, 0, 1])
    r, g, b = bitmap_to_components_rgb565(bitmap)
    symmetric_filter_inplace(r, coefficients, scale=1)
    r += 128
    symmetric_filter_inplace(g, coefficients, scale=1)
    g += 128
    symmetric_filter_inplace(b, coefficients, scale=1)
    b += 128
    return bitmap_from_components_inplace_rgb565(bitmap, r, g, b)
