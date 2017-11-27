#!/usr/bin/env python3

import cv2
import cozmo
import numpy as np
from numpy.linalg import inv
import threading
import time

from ar_markers.hamming.detect import detect_markers

from grid import CozGrid
from gui import GUIWindow
from particle import Particle, Robot
from setting import *
from particle_filter import *
from utils import *
from cozmo.util import distance_mm, speed_mmps, degrees

# camera params
camK = np.matrix([[295, 0, 160], [0, 295, 120], [0, 0, 1]], dtype='float32')

#marker size in inches
marker_size = 3.5

# tmp cache
last_pose = cozmo.util.Pose(0,0,0,angle_z=cozmo.util.Angle(degrees=0))

# goal location for the robot to drive to, (x, y, theta)
goal = (6,10,0)

# map
Map_filename = "map_arena.json"


async def image_processing(robot):

    global camK, marker_size

    event = await robot.world.wait_for(cozmo.camera.EvtNewRawCameraImage, timeout=30)

    # convert camera image to opencv format
    opencv_image = np.asarray(event.image)
    
    # detect markers
    markers = detect_markers(opencv_image, marker_size, camK)
    
    # show markers
    for marker in markers:
        marker.highlite_marker(opencv_image, draw_frame=True, camK=camK)
        #print("ID =", marker.id);
        #print(marker.contours);
    cv2.imshow("Markers", opencv_image)

    return markers

#calculate marker pose
def cvt_2Dmarker_measurements(ar_markers):
    
    marker2d_list = []
    
    for m in ar_markers:
        R_1_2, J = cv2.Rodrigues(m.rvec)
        R_1_1p = np.matrix([[0,0,1], [0,-1,0], [1,0,0]])
        R_2_2p = np.matrix([[0,-1,0], [0,0,-1], [1,0,0]])
        R_2p_1p = np.matmul(np.matmul(inv(R_2_2p), inv(R_1_2)), R_1_1p)
        #print('\n', R_2p_1p)
        yaw = -math.atan2(R_2p_1p[2,0], R_2p_1p[0,0])
        
        x, y = m.tvec[2][0] + 0.5, -m.tvec[0][0]
        # print('x =', x, 'y =', y,'theta =', yaw)
        
        # remove any duplate markers
        dup_thresh = 2.0
        find_dup = False
        for m2d in marker2d_list:
            if grid_distance(m2d[0], m2d[1], x, y) < dup_thresh:
                find_dup = True
                break
        if not find_dup:
            marker2d_list.append((x,y,math.degrees(yaw)))

    return marker2d_list


#compute robot odometry based on past and current pose
def compute_odometry(curr_pose, cvt_inch=True):
    global last_pose
    last_x, last_y, last_h = last_pose.position.x, last_pose.position.y, \
        last_pose.rotation.angle_z.degrees
    curr_x, curr_y, curr_h = curr_pose.position.x, curr_pose.position.y, \
        curr_pose.rotation.angle_z.degrees

    if cvt_inch:
        last_x, last_y = last_x / 25.6, last_y / 25.6
        curr_x, curr_y = curr_x / 25.6, curr_y / 25.6

    return [[last_x, last_y, last_h],[curr_x, curr_y, curr_h]]

#particle filter functionality
class ParticleFilter:

    def __init__(self, grid):
        self.particles = Particle.create_random(PARTICLE_COUNT, grid)
        self.grid = grid

    def update(self, odom, r_marker_list):

        # ---------- Motion model update ----------
        self.particles = motion_update(self.particles, odom)

        # ---------- Sensor (markers) model update ----------
        self.particles = measurement_update(self.particles, r_marker_list, self.grid)

        # ---------- Show current state ----------
        # Try to find current best estimate for display
        m_x, m_y, m_h, m_confident = compute_mean_pose(self.particles)
        return (m_x, m_y, m_h, m_confident)


async def run(robot: cozmo.robot.Robot):
    global last_pose
    global grid, gui

    # start streaming
    robot.camera.image_stream_enabled = True

    #start particle filter
    pf = ParticleFilter(grid)
    await robot.set_head_angle(degrees(0)).wait_for_completed()

    ############################################################################
    ######################### YOUR CODE HERE####################################
    while True:
        robo_odom = compute_odometry(robot.pose)
        print(robo_odom)
        vis_markers = await image_processing(robot)
        markers_2d = cvt_2Dmarker_measurements(vis_markers)
        print(markers_2d)
        m_x, m_y, m_h, m_confident = ParticleFilter.update(pf, robo_odom, markers_2d)
        if robot.is_picked_up:
            await robot.say_text("Please put me down!!").wait_for_completed()
            pf = ParticleFilter(grid)
            time.sleep(5)
        elif m_confident:
            print("Going to the goal pose")
            print ("X: {}, Y:{}".format(m_x,m_y))
            x_offset = robot.pose.position.x - m_x
            y_offset = robot.pose.position.y - m_y
            h_offset = robot.pose_angle.degrees - m_h
            goal_pose = cozmo.util.Pose(10*(goal[0]-x_offset), 10*(goal[1]-y_offset), goal[2], angle_z=degrees(goal[2]))
            print(robot.pose)
            await robot.go_to_pose(goal_pose, relative_to_robot=True, in_parallel=False).wait_for_completed()
            await robot.say_text("I did it!!").wait_for_completed()
            print(robot.pose)
            break
        else:
            last_pose = robot.pose
            if abs(m_x  - 130) < 20 and abs(m_y - 90) < 10:
                await robot.turn_in_place(degrees(15)).wait_for_completed()
            else:
                await robot.turn_in_place(degrees(15)).wait_for_completed()
                # await robot.drive_straight(distance_mm(40), speed_mmps(40), False, False,
                #                                            0).wait_for_completed()
        gui.show_mean(m_x, m_y, m_h, m_confident)
        gui.show_particles(pf.particles)
        robbie = Robot(robot.pose.position.x, robot.pose.position.y, robot.pose_angle.degrees)
        gui.show_robot(robbie)
        gui.updated.set()
    ############################################################################


class CozmoThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self, daemon=False)

    def run(self):
        cozmo.run_program(run, use_viewer=False)


if __name__ == '__main__':

    # cozmo thread
    cozmo_thread = CozmoThread()
    cozmo_thread.start()

    # init
    grid = CozGrid(Map_filename)
    gui = GUIWindow(grid)
    gui.start()
