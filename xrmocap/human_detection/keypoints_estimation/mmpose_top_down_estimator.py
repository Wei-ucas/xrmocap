import cv2
import logging
import numpy as np
from tqdm import tqdm
from typing import List, Tuple, Union

from xrmocap.data_structure.keypoints import Keypoints
from xrmocap.utils.ffmpeg_utils import video_to_array
from xrmocap.utils.log_utils import get_logger

try:
    from mmcv import digit_version
    from mmpose import __version__ as mmpose_version
    from mmpose.apis import inference_top_down_pose_model, init_pose_model
    has_mmpose = True
    import_exception = ''
except (ImportError, ModuleNotFoundError):
    has_mmpose = False
    import traceback
    stack_str = ''
    for line in traceback.format_stack():
        if 'frozen' not in line:
            stack_str += line + '\n'
    import_exception = traceback.format_exc() + '\n'
    import_exception = stack_str + import_exception


class MMposeTopDownEstimator:

    def __init__(self,
                 mmpose_kwargs: dict,
                 bbox_thr: float = 0.0,
                 logger: Union[None, str, logging.Logger] = None) -> None:
        """Init a detector from mmdetection.

        Args:
            mmpose_kwargs (dict):
                A dict contains args of mmpose.apis.init_detector.
                Necessary keys: config, checkpoint
                Optional keys: device
            bbox_thr (float, optional):
                Threshold of a bbox. Those have lower scores will be ignored.
                Defaults to 0.0.
            logger (Union[None, str, logging.Logger], optional):
                Logger for logging. If None, root logger will be selected.
                Defaults to None.
        """
        # build the pose model from a config file and a checkpoint file
        self.pose_model = init_pose_model(**mmpose_kwargs)
        # mmpose inference api takes one image per call
        self.batch_size = 1
        self.bbox_thr = bbox_thr
        self.logger = get_logger(logger)
        if not has_mmpose:
            self.logger.error(import_exception)
            raise ModuleNotFoundError(
                'Please install mmpose to run detection.')
        self.use_old_api = False
        if digit_version(mmpose_version) <= digit_version('0.13.0'):
            self.use_old_api = True

    def get_keypoints_convention_name(self) -> str:
        """Get data_source from dataset type in config file of the pose model.

        Returns:
            str:
                Name of the keypoints convention. Must be
                a key of KEYPOINTS_FACTORY.
        """
        return __translate_data_source__(
            self.pose_model.cfg.data['test']['type'])

    def infer_array(self,
                    image_array: Union[np.ndarray, list],
                    bbox_list: Union[tuple, list],
                    disable_tqdm: bool = False,
                    return_heatmap: bool = False,
                    return_bbox: bool = False) -> Tuple[list, list]:
        """Infer frames already in memory(ndarray type).

        Args:
            image_array (Union[np.ndarray, list]):
                BGR image ndarray in shape [n_frame, height, width, 3],
                or a list of image ndarrays in shape [height, width, 3] while
                len(list) == n_frame.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                heatmap_list (list):
                    A list of keypoint heatmaps. len(heatmap_list) == n_frame
                    and the shape of heatmap_list[f] is
                    (n_human, n_keypoints, width, height).
        """
        ret_pose_list = []
        ret_heatmap_list = []
        ret_bbox_list = []
        n_frame = len(image_array)
        for start_index in tqdm(
                range(0, n_frame, self.batch_size), disable=disable_tqdm):
            end_index = min(n_frame, start_index + self.batch_size)
            # mmpose takes only one frame
            img_arr = image_array[start_index]
            person_results = []
            for frame_index in range(start_index, end_index, 1):
                bboxes_in_frame = []
                for bbox in bbox_list[frame_index]:
                    bboxes_in_frame.append({'bbox': bbox})
                person_results = bboxes_in_frame
            if not self.use_old_api:
                img_input = dict(imgs_or_paths=img_arr)
            else:
                img_input = dict(img_or_path=img_arr)

            pose_results, returned_outputs = inference_top_down_pose_model(
                model=self.pose_model,
                person_results=person_results,
                bbox_thr=self.bbox_thr,
                format='xyxy',
                dataset=self.pose_model.cfg.data['test']['type'],
                return_heatmap=return_heatmap,
                outputs=None,
                **img_input)
            frame_pose_results = [
                person_dict['keypoints'] for person_dict in pose_results
            ]
            frame_pose_results = [frame_pose_results]
            ret_pose_list += frame_pose_results
            # returned_outputs[0]['heatmap'].shape 1, 133, 96, 72
            if return_heatmap:
                frame_heatmap_results = [
                    frame_outputs['heatmap']
                    for frame_outputs in returned_outputs
                ]
                ret_heatmap_list += frame_heatmap_results
            if return_bbox:
                frame_bbox_results = [
                    person_dict['bbox'] for person_dict in pose_results
                ]
                frame_bbox_results = [frame_bbox_results]
                ret_bbox_list += frame_bbox_results
        return ret_pose_list, ret_heatmap_list, ret_bbox_list

    def infer_frames(self,
                     frame_path_list: list,
                     bbox_list: Union[tuple, list],
                     disable_tqdm: bool = False,
                     return_heatmap: bool = False,
                     return_bbox: bool = False) -> Tuple[list, list]:
        """Infer frames from file.

        Args:
            frame_path_list (list):
                A list of frames' absolute paths.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                heatmap_list (list):
                    A list of keypoint heatmaps. len(heatmap_list) == n_frame
                    and the shape of heatmap_list[f] is
                    (n_human, n_keypoints, width, height).
        """
        image_array_list = []
        for frame_abs_path in frame_path_list:
            img_np = cv2.imread(frame_abs_path)
            image_array_list.append(img_np)
        ret_pose_list, ret_heatmap_list, ret_boox_list = self.infer_array(
            image_array=image_array_list,
            bbox_list=bbox_list,
            disable_tqdm=disable_tqdm,
            return_heatmap=return_heatmap,
            return_bbox=return_bbox)
        return ret_pose_list, ret_heatmap_list, ret_boox_list

    def infer_video(self,
                    video_path: str,
                    bbox_list: Union[tuple, list],
                    disable_tqdm: bool = False,
                    return_heatmap: bool = False,
                    return_bbox: bool = False) -> Tuple[list, list]:
        """Infer frames from a video file.

        Args:
            video_path (str):
                Path to the video to be detected.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                heatmap_list (list):
                    A list of keypoint heatmaps. len(heatmap_list) == n_frame
                    and the shape of heatmap_list[f] is
                    (n_human, n_keypoints, width, height).
        """
        image_array = video_to_array(input_path=video_path, logger=self.logger)
        ret_pose_list, ret_heatmap_list, ret_boox_list = self.infer_array(
            image_array=image_array,
            bbox_list=bbox_list,
            disable_tqdm=disable_tqdm,
            return_heatmap=return_heatmap,
            return_bbox=return_bbox)
        return ret_pose_list, ret_heatmap_list, ret_boox_list

    def get_keypoints_from_result(
            self, kps2d_list: List[list]) -> Union[Keypoints, None]:
        """Convert returned keypoints2d into an instance of class Keypoints.

        Args:
            kps2d_list (List[list]):
                A list of human keypoints, returned by
                infer methods.
                Shape of the nested lists is
                (n_frame, n_human, n_keypoints, 3).

        Returns:
            Union[Keypoints, None]:
                An instance of Keypoints with mask and
                convention, data type is numpy.
                If no one has been detected in any frame,
                a None will be returned.
        """
        # shape: (n_frame, n_human, n_keypoints, 3)
        n_frame = len(kps2d_list)
        human_count_list = [len(human_list) for human_list in kps2d_list]
        if len(human_count_list) > 0:
            n_human = max(human_count_list)
        else:
            n_human = 0
        for human_list in kps2d_list:
            if len(human_list) > 0:
                n_keypoints = len(human_list[0])
                break
        if n_human > 0:
            kps2d_arr = np.zeros(shape=(n_frame, n_human, n_keypoints, 3))
            mask_arr = np.ones_like(kps2d_arr[..., 0], dtype=np.uint8)
            for f_idx in range(n_frame):
                if len(kps2d_list[f_idx]) <= 0:
                    mask_arr[f_idx, ...] = 0
                    continue
                for h_idx in range(n_human):
                    if h_idx < len(kps2d_list[f_idx]):
                        mask_arr[f_idx, h_idx, ...] = 1
                        kps2d_arr[f_idx,
                                  h_idx, :, :] = kps2d_list[f_idx][h_idx]
                    else:
                        mask_arr[f_idx, h_idx, ...] = 0
            keypoints2d = Keypoints(
                kps=kps2d_arr,
                mask=mask_arr,
                convention=self.get_keypoints_convention_name(),
                logger=self.logger)
        else:
            keypoints2d = None
        return keypoints2d


def __translate_data_source__(mmpose_dataset_name):
    if mmpose_dataset_name == 'TopDownSenseWholeBodyDataset':
        return 'sense_whole_body'
    elif mmpose_dataset_name == 'TopDownCocoWholeBodyDataset':
        return 'coco_wholebody'
    else:
        raise NotImplementedError
