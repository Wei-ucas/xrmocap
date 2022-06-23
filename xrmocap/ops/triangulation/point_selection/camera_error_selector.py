# yapf: disable
import logging
import numpy as np
from typing import Union

from xrmocap.ops.triangulation.builder import build_triangulator
from xrmocap.utils.triangulation_utils import (
    get_valid_views_stats, prepare_triangulate_input,
)
from xrprimer.ops.triangulation.base_triangulator import BaseTriangulator
from .base_selector import BaseSelector

# yapf: enable


class CameraErrorSelector(BaseSelector):

    def __init__(self,
                 target_camera_number: int,
                 triangulator: Union[BaseTriangulator, dict],
                 verbose: bool = True,
                 logger: Union[None, str, logging.Logger] = None) -> None:
        """Select points according to camera reprojection error. This selector
        will disable the worst cameras according to one reprojection result.

        Args:
            target_camera_number (int):
                For each pair of points, how many views are
                chosen.
                Defaults to True.
            triangulator (Union[BaseSelector, dict]):
                Triangulator for reprojection error calculation.
                An instance or config dict.
                Defaults to True.
            verbose (bool, optional):
                Whether to log info like valid views stats.
                Defaults to True.
            logger (Union[None, str, logging.Logger], optional):
                Logger for logging. If None, root logger will be selected.
                Defaults to None.
        """
        super().__init__(verbose=verbose, logger=logger)
        if target_camera_number >= 2:
            self.target_camera_number = target_camera_number
        else:
            self.logger.error('Arg target_camera_number' +
                              ' must be no fewer than 2.\n' +
                              f'target_camera_number: {target_camera_number}')
            raise ValueError
        if isinstance(triangulator, dict):
            self.triangulator = build_triangulator(triangulator)
        else:
            self.triangulator = triangulator

    def get_selection_mask(
            self,
            points: Union[np.ndarray, list, tuple],
            init_points_mask: Union[np.ndarray, list,
                                    tuple] = None) -> np.ndarray:
        """Get a new selection mask from points and init_points_mask. This
        selector will loop triangulate points, disable the one camera with
        largest reprojection error, and loop again until there are
        self.target_camera_number left.

        Args:
            points (Union[np.ndarray, list, tuple]):
                An ndarray or a nested list of points2d, in shape
                [n_view, ..., 2+n], n >= 0.
            init_points_mask (Union[np.ndarray, list, tuple], optional):
                An ndarray or a nested list of mask, in shape
                [n_view, ..., 1].
                If points_mask[index] == 1, points[index] is valid
                for triangulation, else it is ignored.
                If points_mask[index] == np.nan, the whole pair will
                be ignored and not counted by any method.
                Defaults to None.

        Returns:
            np.ndarray:
                An ndarray or a nested list of mask, in shape
                [n_view, ..., 1].
        """
        points, init_points_mask = prepare_triangulate_input(
            camera_number=len(points),
            points=points,
            points_mask=init_points_mask,
            logger=self.logger)
        selected_cameras = self.get_camera_indexes(
            points=points, init_points_mask=init_points_mask)
        points2d_mask = init_points_mask.copy()
        for view_index in range(points2d_mask.shape[0]):
            if view_index not in selected_cameras:
                points2d_mask[view_index, ...] = 0
        init_points_mask_shape = points2d_mask.shape
        n_view = init_points_mask_shape[0]
        # log stats
        if self.verbose:
            _, stats_table = get_valid_views_stats(
                points2d_mask.reshape(n_view, -1, 1))
            self.logger.info(stats_table)
        points2d_mask = points2d_mask.reshape(*init_points_mask_shape)
        return points2d_mask

    def get_camera_indexes(
            self,
            points: Union[np.ndarray, list, tuple],
            init_points_mask: Union[np.ndarray, list, tuple] = None) -> list:
        """Get a list of camera indexes. This selector will loop triangulate
        points, disable the one camera with largest reprojection error, and
        loop again until there are self.target_camera_number left.

        Args:
            points (Union[np.ndarray, list, tuple]):
                An ndarray or a nested list of points2d, in shape
                [n_view, ..., 2+n], n >= 0.
                [...] could be [n_keypoints],
                [n_frame, n_keypoints],
                [n_frame, n_person, n_keypoints], etc.
            init_points_mask (Union[np.ndarray, list, tuple], optional):
                An ndarray or a nested list of mask, in shape
                [n_view, ..., 1].
                If points_mask[index] == 1, points[index] is valid
                for triangulation, else it is ignored.
                If points_mask[index] == np.nan, the whole pair will
                be ignored and not counted by any method.
                Defaults to None.

        Returns:
            list:
                A list of sorted camera indexes,
                length == self.target_camera_number.
        """
        points, init_points_mask = prepare_triangulate_input(
            camera_number=len(points),
            points=points,
            points_mask=init_points_mask,
            logger=self.logger)
        # backup shape
        init_points_mask_shape = init_points_mask.shape
        n_view = init_points_mask_shape[0]
        # check if there's potential to search
        remain_cameras = np.array([x for x in range(n_view)])
        if n_view == 2:
            self.logger.warning(
                'There\'s no potential to search a sub-triangulator' +
                ' according to n_view.')
        else:
            points3d = self.triangulator.triangulate(
                points=points, points_mask=init_points_mask)
            error = self.triangulator.get_reprojection_error(
                points2d=points,
                points3d=points3d,
                points_mask=init_points_mask)
            abs_error = np.abs(error)
            mean_errors = np.nanmean(
                abs_error.reshape(n_view, -1), axis=1, keepdims=False)
            # get mean error ignoring nan
            min_error_indexes = np.argpartition(
                mean_errors,
                self.target_camera_number)[:self.target_camera_number]
            remain_cameras = sorted(remain_cameras[min_error_indexes].tolist())
        return remain_cameras
