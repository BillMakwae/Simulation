import datetime
import json
import logging
import math
import os
import sys

import numpy as np
import polyline
import pytz
import requests
from dotenv import load_dotenv
from timezonefinder import TimezoneFinder
from tqdm import tqdm

from data.route.__init__ import route_directory
from simulation.common import helpers


class GIS:
    def __init__(self, api_key, origin_coord, dest_coord, waypoints, race_type, force_update=False):
        """
        Initialises a GIS (geographic location system) object. This object is responsible for getting the
        simulation's planned route from the Google Maps API and performing operations on the received data.

        :param api_key: API key that allows access to the Google Maps API
        :param origin_coord: NumPy array containing the start coordinate (lat, long) of the planned travel route
        :param dest_coord: NumPy array containing the end coordinate (lat, long) of the planned travel route
        :param waypoints: NumPy array containing the route waypoints to travel through during simulation
        :param race_type: String ("FSGP" or "ASC") stating which race is simulated
        :param force_update: this argument allows you to update the cached route data by calling the Google Maps API.

        """

        self.api_key = api_key

        self.current_index = 0
        self.distance_remainder = 0

        self.origin_coord = origin_coord
        self.dest_coord = dest_coord
        self.waypoints = waypoints
        self.race_type = race_type

        # path to file storing the route and elevation NumPy arrays
        if self.race_type == "FSGP":
            route_file = route_directory / "route_data_FSGP.npz"
        else:
            route_file = route_directory / "route_data.npz"

        api_call_required = True

        # if the file exists, load path from file
        if os.path.isfile(route_file) and force_update is False:
            with np.load(route_file) as route_data:
                if np.array_equal(route_data['origin_coord'], self.origin_coord) \
                        and np.array_equal(route_data['dest_coord'], self.dest_coord) \
                        and np.array_equal(route_data['waypoints'], self.waypoints):

                    api_call_required = False

                    print("Previous route save file is being used...\n")

                    print("----- Route save file information -----")
                    for key in route_data:
                        print(f"> {key}: {route_data[key].shape}")
                    print()

                    self.path = route_data['path']
                    self.path_elevations = route_data['elevations']
                    self.path_time_zones = route_data['time_zones']

        if api_call_required or force_update:
            logging.warning("New route requested and/or route save file does not exist. "
                            "Calling Google API and creating new route save file...\n")
            logging.warning(
                "The GIS class is collecting data from a Google API. Set force_update to false to prevent this and "
                "read data from disk instead. This should improve performance. \n")
            self.path = self.update_path(self.origin_coord, self.dest_coord, self.waypoints)
            self.path_elevations = self.calculate_path_elevations(self.path)
            self.path_time_zones = self.calculate_time_zones(self.path)

            with open(route_file, 'wb') as f:
                np.savez(f, path=self.path, elevations=self.path_elevations, time_zones=self.path_time_zones,
                         origin_coord=self.origin_coord,
                         dest_coord=self.dest_coord, waypoints=self.waypoints)
        if race_type == "FSGP":
            self.singlelap_path = self.path
            self.path = np.tile(self.path, (60, 1))
            self.path_elevations = np.tile(self.path_elevations, 60)
            self.path_time_zones = self.calculate_time_zones(self.path)

        self.path_distances = helpers.calculate_path_distances(self.path)
        self.path_gradients = helpers.calculate_path_gradients(self.path_elevations,
                                                               self.path_distances)

    def calculate_closest_gis_indices(self, cumulative_distances):
        """
        Takes in an array of point distances from starting point, returns a list of 
        self.path indices of coordinates which have a distance from the starting point
        closest to the point distances

        :param cumulative_distances: (float[N]) array of distances,
        where cumulative_distances[x] > cumulative_distances[x-1]
        
        :returns: (float[N]) array of indices of path
        """

        current_coordinate_index = 0
        result = []

        path_distances = self.path_distances.copy()
        cumulative_path_distances = np.cumsum(path_distances)
        cumulative_path_distances[::2] *= -1
        average_distances = np.abs(np.diff(cumulative_path_distances) / 2)

        with tqdm(total=len(cumulative_distances), file=sys.stdout, desc="Calculating closest GIS indices") as pbar:
            for distance in np.nditer(cumulative_distances):
                if distance > average_distances[current_coordinate_index]:
                    if current_coordinate_index > len(average_distances) - 1:
                        current_coordinate_index = len(average_distances) - 1
                    else:
                        current_coordinate_index += 1
                        if current_coordinate_index > len(average_distances) - 1:
                            current_coordinate_index = len(average_distances) - 1

                result.append(current_coordinate_index)

                pbar.update(1)

        print()

        return np.array(result)

    def calculate_time_zones(self, coords):
        """
        Takes in an array of coordinates, return the time zone relative to UTC, of each location in seconds

        :param coords: (float[N][lat lng]) array of coordinates

        :returns time_diff: (float[N]) array of time differences in seconds
        """

        timezones_return = np.zeros(len(coords))

        tf = TimezoneFinder()

        if self.race_type == "FSGP":
            # this is when FSGP 2021 starts
            dt = datetime.datetime(2021, 7, 31)
        else:
            # this is when ASC 2021 starts
            dt = datetime.datetime(2021, 8, 4)

        for index, coord in enumerate(coords):
            tz_string = tf.timezone_at(lat=coord[0], lng=coord[1])
            timezone = pytz.timezone(tz_string)

            timezones_return[index] = timezone.utcoffset(dt).total_seconds()

        return timezones_return

    def get_time_zones(self, gis_indices):
        """
        Takes in an array of path indices, returns the time zone at each index

        :param gis_indices: (float[N]) array of path indices

        :returns: (float[N]) array of time zones in seconds
        """

        return self.path_time_zones[gis_indices]

    # ----- Getters -----

    def get_gradients(self, gis_indices):
        """
        Takes in an array of path indices, returns the road gradient at each index

        :param gis_indices: (float[N]) array of path indices

        :returns: (float[N]) array of road gradients
        """

        return self.path_gradients[gis_indices]

    def get_path(self):
        """
        Returns all N coordinates of the path in a NumPy array
        [N][latitude, longitude]
        """

        return self.path

    def get_path_elevations(self):
        """
        Returns all N elevations of the path in a NumPy array
        [N][elevation]
        """

        return self.path_elevations

    def get_path_distances(self):
        """
        Returns all N-1 distances of the path in a NumPy array
        [N-1][elevation]
        """

        return self.path_distances

    def get_path_gradients(self):
        """
        Returns all N-1 gradients of a path in a NumPy array
        [N-1][gradient]
        """

        return self.path_gradients

    # ----- Path calculation functions -----

    def update_path(self, origin_coord, dest_coord, waypoints):
        """
        Returns a path between the origin coordinate and the destination coordinate,
        passing through a group of optional waypoints.

        origin_coord: A NumPy array [latitude, longitude] of the starting coordinate
        dest_coord: A NumPy array [latitude, longitude] of the destination coordinate
        waypoint: A NumPy array [n][latitude, longitude], where n<=10

        Returns: A NumPy array [n][latitude, longitude], marking out the path.

        https://developers.google.com/maps/documentation/directions/start
        """

        # set up URL
        url_head = f"https://maps.googleapis.com/maps/api/directions/json?origin={origin_coord[0]},{origin_coord[1]}" \
                   f"&destination={dest_coord[0]},{dest_coord[1]}"

        url_waypoints = ""
        if len(waypoints) != 0:

            url_waypoints = "&waypoints="

            if len(waypoints) > 10:
                print("Too many waypoints; Truncating to 10 waypoints total")
                waypoints = waypoints[0:10]

            for waypoint in waypoints:
                url_waypoints = url_waypoints + f"via:{waypoint[0]},{waypoint[1]}|"

            url_waypoints = url_waypoints[:-1]

        url_end = f"&key={self.api_key}"

        url = url_head + url_waypoints + url_end

        # HTTP GET
        r = requests.get(url)
        response = json.loads(r.text)

        path_points = []

        # If a route is found...
        if response['status'] == "OK":
            print("A route was found.\n")

            # Pick the first route in the list of available routes
            # A route consists of a series of legs
            for leg in response['routes'][0]['legs']:

                # Every leg contains an array of steps.
                for step in leg['steps']:
                    # every step contains an encoded polyline
                    polyline_raw = step['polyline']['points']
                    polyline_coords = polyline.decode(polyline_raw)
                    path_points = path_points + polyline_coords

            print("Route has been successfully retrieved!\n")

        else:
            print(f"No route was found: {response['status']}")
            print(f"Error Message: {response['error_message']}")

        route = np.array(path_points)

        # Removes duplicate coordinates to prevent gradient calculation errors
        if route.size != 0:
            duplicate_coordinate_indices = np.where((np.diff(route[:, 0]) == 0)) and np.where(
                (np.diff(route[:, 1]) == 0))
            route = np.delete(route, duplicate_coordinate_indices, axis=0)

        return route

    def calculate_path_min_max(self):
        min_lat, min_long = self.path.min(axis=0)
        max_lat, max_long = self.path.max(axis=0)
        return [min_long, min_lat, max_long, max_lat]

    def calculate_path_elevations(self, coords):
        """
        Returns the elevations of every coordinate in the array of coordinates passed in as a coordinate

        See Error Message Interpretations: https://developers.google.com/maps/documentation/elevation/overview

        :param coords: A NumPy array [n][latitude, longitude]
        :returns: A NumPy array [n][elevation] in metres
        """

        # construct URL
        url_head = 'https://maps.googleapis.com/maps/api/elevation/json?locations='

        location_strings = []
        locations = ""

        for coord in coords:

            locations = locations + f"{coord[0]},{coord[1]}|"

            if len(locations) > 8000:
                location_strings.append(locations[:-1])
                locations = ""

        if len(locations) != 0:
            location_strings.append(locations[:-1])

        url_tail = "&key={}".format(self.api_key)

        # Get elevations
        elevations = np.zeros(len(coords))

        i = 0
        for location_string in location_strings:
            url = url_head + location_string + url_tail

            r = requests.get(url)
            response = json.loads(r.text)
        if response['status'] == "OK":
            for result in response['results']:
                elevations[i] = result['elevation']
                i = i + 1

        elif response['status'] == "INVALID_REQUEST":
            sys.stderr.write("Error: Request was invalid\n")

        elif response['status'] == "OVER_DAILY_LIMIT":
            sys.stderr.write(
                "Error: Possible causes - API key is missing or invalid, billing has not been enabled,"
                " a self-imposed usage cap has been exceeded, or the provided payment method is no longer valid\n")

        elif response['status'] == "OVER_QUERY_LIMIT":
            sys.stderr.write("Error: Requester has exceeded quota\n")

        elif response['status'] == "REQUEST_DENIED":
            sys.stderr.write("Error: API could not complete the request\n")

        return elevations

    def calculate_current_heading_array(self):
        """
        Calculates the bearing of the vehicle between consecutive points
        https://www.movable-type.co.uk/scripts/latlong.html
        """
        bearing_array = np.zeros(len(self.path))

        for index in range(0, len(self.path) - 1):
            coord_1 = np.radians(self.path[index])
            coord_2 = np.radians(self.path[index + 1])

            y = math.sin(coord_2[1] - coord_1[1]) \
                * math.cos(coord_2[0])

            x = math.cos(coord_1[0]) \
                * math.sin(coord_2[0]) \
                - math.sin(coord_1[0]) \
                * math.cos(coord_2[0]) \
                * math.cos(coord_2[1] - coord_1[1])

            theta = math.atan2(y, x)

            bearing_array[index] = ((theta * 180) / math.pi + 360) % 360

        bearing_array[-1] = bearing_array[-2]

        return bearing_array

    def update_vehicle_position(self, incremental_distance):
        """
        Returns the closest coordinate to the current coordinate

        :param incremental_distance: distance in m covered in the latest tick

        :returns: The new index of the vehicle
        """

        additional_distance = self.distance_remainder + incremental_distance

        # while the index of position can still be advanced
        while additional_distance > 0:
            # subtract contributions from every new index
            additional_distance = additional_distance - self.path_distances[self.current_index]

            # advance the index
            self.current_index = self.current_index + 1

        # backtrack a bit
        self.distance_remainder = additional_distance + self.path_distances[self.current_index - 1]
        self.current_index = self.current_index - 1

        return self.current_index

if __name__ == "__main__":
    load_dotenv()
    google_api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    simulation_duration = 1 * 60 * 60

    origin_coord = np.array([38.9281815, -95.6770217])
    dest_coord = np.array([38.9282115, -95.6770268])
    waypoints = np.array([
        [38.9221906, -95.6762981],
        [38.9217086, -95.6767896], [38.9189926, -95.6753145], [38.9196768, -95.6724799],
        [38.9196768, -95.6724799], [38.9247448, -95.6714528], [38.9309102, -95.6749362],
        [38.928188, -95.6770129]
    ])

    locationSystem = GIS(api_key=google_api_key, origin_coord=origin_coord, dest_coord=dest_coord, waypoints=waypoints,
                         race_type="FSGP", force_update=False)

    locationSystem.tile_route(simulation_duration=simulation_duration)

    # path = locationSystem.get_path()
    # print(path)
