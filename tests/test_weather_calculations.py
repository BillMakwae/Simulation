import numpy as np
from simulation.environment import WeatherForecasts

def test_check_cloud_cover_irradiance_minimum():
    ghi = np.array([500, 300, 100])
    cloud_covers = np.array([100, 100, 100])

    result = WeatherForecasts.cloud_cover_to_ghi_linear(cloud_covers, ghi)

    assert np.all(result == np.array([175, 105, 35]))

def test_check_cloud_cover_irradiance():
    ghi = np.array([500, 300, 100])
    cloud_covers = np.array([80, 0, 30])

    result = WeatherForecasts.cloud_cover_to_ghi_linear(cloud_covers, ghi)

    assert np.all(result == np.array([240, 300, 80.5]))
