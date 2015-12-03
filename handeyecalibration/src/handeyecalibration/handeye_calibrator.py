import rospy
import tf
from geometry_msgs.msg import Vector3, Quaternion, Transform
from visp_hand2eye_calibration.msg import TransformArray
from visp_hand2eye_calibration.srv import compute_effector_camera_quick
from handeyecalibration.handeye_calibration import HandeyeCalibration


class HandeyeCalibrator(object):
    """
    Connects tf and ViSP hand2eye to provide an interactive mean of calibration.
    """

    MIN_SAMPLES = 2  # TODO: correct? this is what is stated in the paper, but sounds strange
    """Minimum samples required for a successful calibration."""

    def __init__(self):
        self.eye_on_hand = rospy.get_param('eye_on_hand', False)
        """
        if false, it is a eye-on-base calibration

        :type: bool
        """

        self.base_link_frame = None
        """
        robot base tf name

        :type: string
        """

        self.tool_frame = None
        """
        robot tool tf name

        :type: string
        """

        # tf names
        self.tool_frame = rospy.get_param('tool_frame', 'tool0')
        self.base_link_frame = rospy.get_param('base_link_frame', 'base_link')

        self.optical_origin_frame = rospy.get_param('optical_origin_frame', 'optical_origin')
        self.optical_target_frame = rospy.get_param('optical_target_frame', 'optical_target')

        # tf structures
        self.listener = tf.TransformListener()
        self.broadcaster = tf.TransformBroadcaster()
        self.transformer = tf.TransformerROS()  # for converting messages to rotation matrices, etc.

        # internal input data
        self.samples = []

        # VISP input data
        self.hand_world_samples = TransformArray()
        self.camera_marker_samples = TransformArray()

        # calibration service
        rospy.wait_for_service('compute_effector_camera_quick')
        self.calibrate = rospy.ServiceProxy(
            'compute_effector_camera_quick',
            compute_effector_camera_quick)

    def _wait_for_tf_init(self):
        self.listener.waitForTransform(self.base_link_frame, self.tool_frame, rospy.Time(0), rospy.Duration(10))
        self.listener.waitForTransform(self.optical_origin_frame, self.optical_target_frame, rospy.Time(0),
                                       rospy.Duration(60))

    def _wait_for_transforms(self):
        now = rospy.Time.now()
        self.listener.waitForTransform(self.base_link_frame, self.tool_frame, now, rospy.Duration(10))
        self.listener.waitForTransform(self.optical_origin_frame, self.optical_target_frame, now, rospy.Duration(10))
        return now

    def _get_transforms(self, time=None):
        if time is None:
            time = self._wait_for_transforms()

        rob = None
        if self.eye_on_hand:
            rob = self.listener.lookupTransform(self.base_link_frame, self.tool_frame,
                                                time)
        else:
            rob = self.listener.lookupTransform(self.tool_frame, self.base_link_frame,
                                                time)
        opt = self.listener.lookupTransform(self.optical_origin_frame, self.optical_target_frame, time)
        return {'robot': rob, 'optical': opt}

    def take_sample(self):
        rospy.loginfo("Taking a sample...")
        transforms = self._get_transforms()
        rospy.loginfo("Got a sample")
        self.samples.append(transforms)

    def remove_sample(self, index):
        if 0 <= index < len(self.samples):
            del self.samples[index]

    @staticmethod
    def _tuple_to_visp_transform(tf_t):
        transl = Vector3(*tf_t[0])
        rot = Quaternion(*tf_t[1])
        return Transform(transl, rot)

    def get_visp_samples(self):
        hand_world_samples = TransformArray()
        hand_world_samples.header.frame_id = self.optical_origin_frame  # TODO: ???

        camera_marker_samples = TransformArray()
        camera_marker_samples.header.frame_id = self.optical_origin_frame

        for s in self.samples:
            to = HandeyeCalibrator._tuple_to_visp_transform(s['optical'])
            camera_marker_samples.transforms.append(to)
            tr = HandeyeCalibrator._tuple_to_visp_transform(s['robot'])
            hand_world_samples.transforms.append(tr)

        return hand_world_samples, camera_marker_samples

    def compute_calibration(self):

        if len(self.samples) < HandeyeCalibrator.MIN_SAMPLES:
            rospy.logwarn("%d more samples needed..." % (HandeyeCalibrator.MIN_SAMPLES - len(self.samples)))
            return

        # Update data
        hand_world_samples, camera_marker_samples = self.get_visp_samples()

        if len(hand_world_samples.transforms) != len(camera_marker_samples.transforms):
            rospy.logerr("Different numbers of hand-world and camera-marker samples.")
            return

        rospy.loginfo("Computing from %g poses..." % len(self.samples))

        try:
            result = self.calibrate(camera_marker_samples, hand_world_samples)
            transl = result.effector_camera.translation
            rot = result.effector_camera.rotation
            result_tf = Transform((transl.x,
                                   transl.y,
                                   transl.z),
                                  (rot.x,
                                   rot.y,
                                   rot.z,
                                   rot.w))

            ret = HandeyeCalibration(self.eye_on_hand,
                                     self.base_link_frame,
                                     self.tool_frame,
                                     self.optical_origin_frame,
                                     result_tf)
            return ret

        except rospy.ServiceException as ex:
            rospy.logerr("Calibration failed: " + str(ex))
            return None
