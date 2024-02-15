import time
import SimpleITK as sitk
import numpy as np
np.lib.index_tricks.int = np.uint16
import ants
from os.path import join
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
import json
from custom_algorithm import Hanseg2023Algorithm

LABEL_dict = {
    "background": 0,
    "A_Carotid_L": 1,
    "A_Carotid_R": 2,
    "Arytenoid": 3,
    "Bone_Mandible": 4,
    "Brainstem": 5,
    "BuccalMucosa": 6,
    "Cavity_Oral": 7,
    "Cochlea_L": 8,
    "Cochlea_R": 9,
    "Cricopharyngeus": 10,
    "Esophagus_S": 11,
    "Eye_AL": 12,
    "Eye_AR": 13,
    "Eye_PL": 14,
    "Eye_PR": 15,
    "Glnd_Lacrimal_L": 16,
    "Glnd_Lacrimal_R": 17,
    "Glnd_Submand_L": 18,
    "Glnd_Submand_R": 19,
    "Glnd_Thyroid": 20,
    "Glottis": 21,
    "Larynx_SG": 22,
    "Lips": 23,
    "OpticChiasm": 24,
    "OpticNrv_L": 25,
    "OpticNrv_R": 26,
    "Parotid_L": 27,
    "Parotid_R": 28,
    "Pituitary": 29,
    "SpinalCord": 30,
}


def ants_2_itk(image):
    imageITK = sitk.GetImageFromArray(image.numpy().T)
    imageITK.SetOrigin(image.origin)
    imageITK.SetSpacing(image.spacing)
    imageITK.SetDirection(image.direction.reshape(9))
    return imageITK

def itk_2_ants(image):
    image_ants = ants.from_numpy(sitk.GetArrayFromImage(image).T,
                                 origin=image.GetOrigin(),
                                 spacing=image.GetSpacing(),
                                 direction=np.array(image.GetDirection()).reshape(3, 3))
    return image_ants


class MyHanseg2023Algorithm(Hanseg2023Algorithm):
    def __init__(self):
        super().__init__()

    def predict(self, *, image_ct: ants.ANTsImage, image_mrt1: ants.ANTsImage) -> sitk.Image:
        print("Computing registration", flush=True)
        time0reg= time.time_ns()
        mytx = ants.registration(fixed=image_ct, moving=image_mrt1, type_of_transform='Elastic', aff_iterations=(50, 50, 50, 50))
        print(f"Time reg: {(time.time_ns()-time0reg)/1000000000}")
        warped_MR = ants.apply_transforms(fixed=image_ct, moving=image_mrt1,
                                          transformlist=mytx['fwdtransforms'])
        trained_model_path = join("/opt", "algorithm", "checkpoint", "nnUNet", "Dataset504_HANSEGREG", "STUNetTrainer_base_ft__nnUNetPlans__3d_fullres")

        spacing = tuple(map(float,json.load(open(join(trained_model_path, "plans.json"), "r"))["configurations"]["3d_fullres"]["spacing"]))
        ct_image = ants_2_itk(image_ct)
        mr_image = ants_2_itk(warped_MR)
        del image_mrt1
        del warped_MR

        properties = {
            'sitk_stuff':
                {'spacing': ct_image.GetSpacing(),
                 'origin': ct_image.GetOrigin(),
                 'direction': ct_image.GetDirection()
                },
            # the spacing is inverted with [::-1] because sitk returns the spacing in the wrong order lol. Image arrays
            # are returned x,y,z but spacing is returned z,y,x. Duh.
            'spacing': ct_image.GetSpacing()[::-1]
        }
        images = np.vstack([sitk.GetArrayFromImage(ct_image)[None], sitk.GetArrayFromImage(mr_image)[None]]).astype(np.float32)
        fin_origin = ct_image.GetOrigin()
        fin_spacing = ct_image.GetSpacing()
        fin_direction = ct_image.GetDirection()
        fin_size = ct_image.GetSize()
        print(fin_spacing)
        print(spacing)
        print(fin_size)

        old_shape = np.shape(sitk.GetArrayFromImage(ct_image))
        del mr_image
        del ct_image
        # Shamelessly copied from nnUNet/nnunetv2/preprocessing/resampling/default_resampling.py
        new_shape = np.array([int(round(i / j * k)) for i, j, k in zip(fin_spacing, spacing[::-1], fin_size)])
        if new_shape.prod()<  1e8:
            print(f"Image is not too large ({new_shape.prod()}), using the folds (0,1,2,3,4) with mirror")
            predictor = nnUNetPredictor(tile_step_size=0.4, use_mirroring=True, perform_everything_on_gpu=True,
                                        verbose=True, verbose_preprocessing=True,
                                        allow_tqdm=True)
            predictor.initialize_from_trained_model_folder(trained_model_path, use_folds=(0,1,2,3,4),
                                                           checkpoint_name="checkpoint_final.pth")
            # predictor.allowed_mirroring_axes = (0, 2)
        elif new_shape.prod()< 1.3e8:
            print(f"Image is not too large ({new_shape.prod()}), using the folds (0,1,2,3,4)")

            predictor = nnUNetPredictor(tile_step_size=0.6, use_mirroring=True, perform_everything_on_gpu=False,
                                        verbose=True, verbose_preprocessing=True,
                                        allow_tqdm=True)
            predictor.initialize_from_trained_model_folder(trained_model_path, use_folds=(0,1,2,3,4),
                                                           checkpoint_name="checkpoint_final.pth")
        elif new_shape.prod()< 1.7e8:
            print(f"Image is not too large ({new_shape.prod()}), using the 'all' fold with mirror")

            predictor = nnUNetPredictor(tile_step_size=0.4, use_mirroring=True, perform_everything_on_gpu=False,
                                        verbose=True, verbose_preprocessing=True,
                                        allow_tqdm=True)
            predictor.initialize_from_trained_model_folder(trained_model_path, use_folds="all",
                                                           checkpoint_name="checkpoint_final.pth")
            # predictor.allowed_mirroring_axes = (0, 2)

        else:
            predictor = nnUNetPredictor(tile_step_size=0.6, use_mirroring=True, perform_everything_on_gpu=False,
                                        verbose=True, verbose_preprocessing=True,
                                        allow_tqdm=True)
            print(f"Image is too large ({new_shape.prod()}), using the 'all' fold")
            predictor.initialize_from_trained_model_folder(trained_model_path, use_folds="all",
                                                           checkpoint_name="checkpoint_final.pth")

        img_temp = predictor.predict_single_npy_array(images, properties, None, None, False).astype(np.uint8)
        del images
        print("Prediction Done", flush=True)
        output_seg = sitk.GetImageFromArray(img_temp)
        print(f"Seg: {output_seg.GetSize()}, CT: {fin_size}")
        # output_seg.CopyInformation(ct_image)
        output_seg.SetOrigin(fin_origin)
        output_seg.SetSpacing(fin_spacing)
        output_seg.SetDirection(fin_direction)
        print("Got Image", flush=True)
        return output_seg

if __name__ == "__main__":
    time0 = time.time_ns()
    MyHanseg2023Algorithm().process()
    print((time.time_ns()-time0)/1000000000)
