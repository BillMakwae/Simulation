import sys
import simulation
import numpy as np
import datetime
import seaborn as sns
import pandas as pd
from simulation.common import helpers
import matplotlib.pyplot as plt
from tqdm import tqdm

"""
Description: Given an hourly driving speed, find the range at the speed
before the battery runs out [speed -> distance].
"""


class ExampleSimulation:

    def __init__(self, google_api_key, weather_api_key, origin_coord, dest_coord, waypoints, tick, simulation_duration):
        """
        Instantiates a simple model of the car

        lvs_power_loss: power loss in Watts due to the lvs system
        max_speed: maximum speed of the vehicle on horizontal ground, with no wind
        """

        # TODO: replace max_speed with a direct calculation taking into account the
        #   elevation and wind_speed
        # TODO: max speed at any given moment actually depends on the elevation of the road, and the wind speed

        # ----- Simulation constants -----

        self.initial_battery_charge = 1.0

        # LVS power loss is pretty small so it is neglected
        self.lvs_power_loss = 0

        self.max_speed = 104

        # ----- Time constants -----

        self.tick = tick
        self.simulation_duration = simulation_duration

        # ----- API keys -----

        self.google_api_key = google_api_key
        self.weather_api_key = weather_api_key

        # ----- Route constants -----

        self.origin_coord = origin_coord
        self.dest_coord = dest_coord
        self.waypoints = waypoints

        # ----- Component initialisation -----

        self.basic_array = simulation.BasicArray()

        self.basic_battery = simulation.BasicBattery(self.initial_battery_charge)

        self.basic_lvs = simulation.BasicLVS(self.lvs_power_loss * self.tick)

        self.basic_motor = simulation.BasicMotor()

        self.gis = simulation.GIS(self.google_api_key, self.origin_coord, self.dest_coord, self.waypoints)
        self.route_coords = self.gis.get_path()
        self.vehicle_bearings = self.gis.calculate_current_heading_array()

        self.weather = simulation.WeatherForecasts(self.weather_api_key, self.route_coords,
                                                   self.simulation_duration / 3600,
                                                   weather_data_frequency="daily")
        self.time_of_initialization = self.weather.last_updated_time

        self.solar_calculations = simulation.SolarCalculations()

    @helpers.timeit
    def run_model(self, speed, plot_results=True):
        """
        Updates the model in tick increments for the entire simulation duration. Returns
        a final battery charge and a distance travelled for this duration, given an
        initial charge, and a target speed. Also requires the current time and location.
        This is where the magic happens.

        Note: if the speed remains constant throughout this update, and knowing the starting 
            time, the cumulative distance at every time can be known. From the cumulative 
            distance, the GIS class updates the new location of the vehicle. From the location
            of the vehicle at every tick, the gradients at every tick, the weather at every
            tick, the GHI at every tick, is known.

        Note 2: currently, the simulation can only be run for times during which weather data is available
        """

        # ----- Reshape speed array -----

        print(f"Input speeds: {speed}\n")

        speed_kmh = helpers.reshape_and_repeat(speed, self.simulation_duration)
        speed_kmh = np.insert(speed_kmh, 0, 0)
        speed_kmh = helpers.add_accelereation(speed_kmh,500)
        # ----- Expected distance estimate -----

        # Array of cumulative distances hopefully travelled in this round
        timestamps = np.arange(0, self.simulation_duration + self.tick, self.tick)
        tick_array = np.diff(timestamps)
        tick_array = np.insert(tick_array, 0, 0)

        # Array of cumulative distances obtained from the timestamps
        # TODO: there needs to be some kind of mechanism that stops the car travelling the max distance
        #   and past the last coordinate in the provided route
        distances = tick_array * speed_kmh
        cumulative_distances = np.cumsum(distances)

        # ----- Weather and location calculations -----

        """ closest_gis_indices is a 1:1 mapping between each point which has within it a timestamp and cumulative
                distance from a starting point, to its closest point on a map.
                
            closest_weather_indices is a 1:1 mapping between a weather condition, and its closest point on a map."""

        closest_gis_indices = self.gis.calculate_closest_gis_indices(cumulative_distances)
        closest_weather_indices = self.weather.calculate_closest_weather_indices(cumulative_distances)

        # Array of elevations at every route point
        gis_route_elevations = self.gis.get_path_elevations()
        gis_route_elevations_at_each_tick = gis_route_elevations[closest_gis_indices]

        # Get the azimuth angle of the vehicle at every location
        gis_vehicle_bearings = self.vehicle_bearings[closest_gis_indices]

        # Get array of path gradients
        gradients = self.gis.get_gradients(closest_gis_indices)

        # Get the time zones of all the starting times
        time_zones = self.gis.get_time_zones(closest_gis_indices)

        local_times = self.gis.adjust_timestamps_to_local_times(timestamps, self.time_of_initialization, time_zones)

        # Get the weather at every location
        weather_forecasts = self.weather.get_weather_forecast_in_time(closest_weather_indices, local_times)
        absolute_wind_speeds = weather_forecasts[:, 5]
        wind_directions = weather_forecasts[:, 6]
        cloud_covers = weather_forecasts[:, 7]

        # Get the wind speeds at every location
        wind_speeds = self.weather.get_array_directional_wind_speed(gis_vehicle_bearings, absolute_wind_speeds,
                                                                    wind_directions)

        # Get an array of solar irradiance at every coordinate and time
        solar_irradiances = self.solar_calculations.calculate_array_GHI(self.route_coords[closest_gis_indices],
                                                                        time_zones, local_times,
                                                                        gis_route_elevations[closest_gis_indices],
                                                                        cloud_covers)

        # TLDR: we have now obtained solar irradiances, wind speeds, and gradients at each tick

        # ----- Energy calculations -----

        self.basic_lvs.update(self.tick)

        lvs_consumed_energy = self.basic_lvs.get_consumed_energy()
        motor_consumed_energy = self.basic_motor.calculate_energy_in(speed_kmh, gradients, wind_speeds, self.tick)
        array_produced_energy = self.basic_array.calculate_produced_energy(solar_irradiances, self.tick)

        consumed_energy = motor_consumed_energy + lvs_consumed_energy
        produced_energy = array_produced_energy

        # net energy added to the battery
        delta_energy = produced_energy - consumed_energy

        # ----- Array initialisation -----

        # used to calculate the time the car was in motion
        tick_array = np.full_like(timestamps, fill_value=self.tick, dtype='f4')
        tick_array[0] = 0

        # ----- Array calculations -----

        cumulative_delta_energy = np.cumsum(delta_energy)
        battery_variables_array = self.basic_battery.update_array(cumulative_delta_energy)

        # stores the battery SOC at each time step
        state_of_charge = battery_variables_array[0]
        state_of_charge[np.abs(state_of_charge) < 1e-03] = 0

        # when the battery is empty the car will not move
        # TODO: if the car cannot climb the slope, the car also does not move
        speed_kmh = np.logical_and(speed_kmh, state_of_charge) * speed_kmh
        time_in_motion = np.logical_and(tick_array, state_of_charge) * self.tick

        time_taken = np.sum(time_in_motion)
        time_taken = str(datetime.timedelta(seconds=int(time_taken)))

        final_soc = state_of_charge[-1] * 100 + 0.

        # ----- Target value -----

        distance = speed_kmh * (time_in_motion / 3600)

        distance_travelled = np.sum(distance)

        print(f"Simulation successful!\n"
              f"Time taken: {time_taken}\n"
              f"Maximum distance traversable: {distance_travelled:.2f}km\n"
              f"Average speed: {np.average(speed_kmh):.2f}km/h\n"
              f"Final battery SOC: {final_soc:.2f}%\n")

        # ----- Plotting -----

        if plot_results:
            arrays_to_plot = [speed_kmh, np.cumsum(distance), state_of_charge, delta_energy,
                              solar_irradiances, wind_speeds, gis_route_elevations_at_each_tick,
                              cloud_covers]

            compress_constant = int(timestamps.shape[0] / 5000)

            for index, array in enumerate(arrays_to_plot):
                arrays_to_plot[index] = array[::compress_constant]

            y_label = ["Speed (km/h)", "Distance (km)", "SOC (%)", "Delta energy (J)",
                       "Solar irradiance (W/m^2)", "Wind speeds (km/h)", "Elevation (m)", "Cloud cover (%)"]
            sns.set_style("whitegrid")
            f, axes = plt.subplots(4, 2, figsize=(12, 8))
            f.suptitle("Simulation results", fontsize=16, weight="bold")

            with tqdm(total=len(arrays_to_plot), file=sys.stdout, desc="Plotting data") as pbar:
                for index, axis in enumerate(axes.flatten()):
                    df = pd.DataFrame(dict(time=timestamps[::compress_constant] / 3600, value=arrays_to_plot[index]))
                    g = sns.lineplot(x="time", y="value", data=df, ax=axis)
                    g.set(xlabel="time (hrs)", ylabel=y_label[index])
                    pbar.update(1)
            print()
            sns.despine()
            plt.setp(axes)
            plt.tight_layout()
            plt.show()


# TODO: put this in a separate file
def main():

    # length of the simulation in seconds
    simulation_length = 60 * 60 * 9

    # input_speed = np.array([100, 87, 65, 89, 43, 54, 45, 23, 34, 20])

    input_speed = np.array([45, 87, 65, 89, 43, 54, 45, 23, 34, 20])

    """
    Note: it no longer matters how many elements the input_speed array has, the simulation automatically
        reshapes the array depending on the simulation_length. 

    Examples:
      If you want a constant speed for the entire simulation, insert a single element
      into the input_speed array. 
      
      >>> input_speed = np.array([30]) <-- constant speed of 30km/h
    
      If you want 50km/h in the first half of the simulation and 60km/h in the second half,
      do the following:

    >>> input_speed = np.array([50, 60])
    
      This logic will apply for all subsequent array lengths (3, 4, 5, etc.)
      
      Keep in mind, however, that the condition len(input_speed) <= simulation_length must be true
    """

    google_api_key = "AIzaSyCPgIT_5wtExgrIWN_Skl31yIg06XGtEHg"
    weather_api_key = "51bb626fa632bcac20ccb67a2809a73b"

    origin_coord = np.array([39.0918, -94.4172])

    waypoints = np.array([[39.0379, -95.6764], [40.8838, -98.3734],
                          [41.8392, -103.7115], [42.8663, -106.3372], [42.8408, -108.7452],
                          [42.3224, -111.2973], [42.5840, -114.4703]])

    dest_coord = np.array([43.6142, -116.2080])

    simulation_model = ExampleSimulation(google_api_key, weather_api_key, origin_coord, dest_coord, waypoints, tick=1,
                                         simulation_duration=simulation_length)
    simulation_model.run_model(speed=input_speed, plot_results=True)


if __name__ == "__main__":
    main()
