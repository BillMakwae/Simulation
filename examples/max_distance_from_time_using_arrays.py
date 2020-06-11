import simulation
import numpy as np
import datetime

"""
Description: Given an input time, determine the largest distance the car can travel in that time. [time -> distance] 
Note: this example assumes constant speed throughout
"""

# ----- Simulation input -----

simulation_duration = 60 * 60 * 9
tick = 1
speed_increment = 1

# ----- Simulation constants -----

incident_sunlight = 1000
initial_battery_charge = 0.9
lvs_power_loss = 0
max_speed = 50

# ----- Component initialisation -----

basic_array = simulation.BasicArray(incident_sunlight)
basic_array.set_produced_energy(0)

basic_battery = simulation.BasicBattery(initial_battery_charge)

basic_lvs = simulation.BasicLVS(lvs_power_loss * tick)

basic_motor = simulation.BasicMotor()

# ----- Array initialisation -----

time = np.linspace(0, simulation_duration, num=int(simulation_duration / tick) + 1, dtype='f4')

speeds_simulated = np.linspace(1, max_speed, num=int(max_speed / speed_increment), dtype='f4')

speed_kmh = np.meshgrid(time, speeds_simulated)[-1]

tick_array = np.full_like(speed_kmh, fill_value=tick, dtype='f4')
tick_array[:, 0] = 0

# ----- Energy calculations -----

basic_array.update(tick)

basic_lvs.update(tick)
lvs_consumed_energy = basic_lvs.get_consumed_energy()

basic_motor.calculate_power_in(speed_kmh)
basic_motor.update(tick)
motor_consumed_energy = basic_motor.get_consumed_energy()

produced_energy = basic_array.get_produced_energy()
consumed_energy = motor_consumed_energy + lvs_consumed_energy

delta_energy = produced_energy - consumed_energy

# ----- Array calculations -----

cumulative_delta_energy = np.cumsum(delta_energy, axis=1)

battery_variables_array = basic_battery.update_array(cumulative_delta_energy)

state_of_charge = battery_variables_array[0].round(3) + 0.

speed_kmh = np.logical_and(speed_kmh, state_of_charge) * speed_kmh
time_in_motion = np.logical_and(tick_array, state_of_charge) * tick

time_taken = np.sum(time_in_motion, axis=1)

final_soc = state_of_charge[:, -1] * 100 + 0.

distance = speed_kmh * (tick_array / 3600)
distance_travelled = np.sum(distance, axis=1)

# ----- Simulation output -----

max_distance = np.amax(distance_travelled)
max_distance_index = np.argmax(distance_travelled)

max_distance_time = time_taken[max_distance_index]
max_distance_time = str(datetime.timedelta(seconds=int(max_distance_time)))

max_distance_speed = speeds_simulated[max_distance_index]

max_distance_final_soc = final_soc[max_distance_index]

print(f"Simulation complete!\n\n"
      f"Time taken: {max_distance_time}\n"
      f"Speed: {max_distance_speed}km/h\n"
      f"Maximum distance traversable: {max_distance:.2f}km\n"
      f"Final battery SOC: {max_distance_final_soc:.2f}%\n")
