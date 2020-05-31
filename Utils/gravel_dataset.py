import os
import shutil
import xml.etree.cElementTree as Et

import torch
import torch.utils.data as data
import torchvision.transforms as transforms
from PIL import Image

from Utils.iou import compute_iou


class GravelDataset(data.Dataset):
    def __init__(self, anchors, image_dir_path, annotation_dir_path, data_list, input_size, scale, device, train):
        self.crossed_image_dir_path, self.single_image_dir_path, self.crossed_image_train_dir_path, self.single_image_train_dir_path = image_dir_path
        self.annotations_dir_path, self.annotations_train_dir_path = annotation_dir_path

        self.input_size = input_size
        self.s_scale, self.m_scale, self.l_scale = scale
        self.s_output_size = self.input_size // self.s_scale
        self.m_output_size = self.input_size // self.m_scale
        self.l_output_size = self.input_size // self.l_scale

        self.dataset_list = data_list

        self.s_anchors, self.m_anchors, self.l_anchors = anchors

        self.device = device

        self.train = train

        self.iou_thresh = 0.3

        if (not os.path.exists(self.crossed_image_train_dir_path)) or (not os.path.exists(self.single_image_train_dir_path)) or \
                (not os.path.exists(self.annotations_train_dir_path)):
            if os.path.exists(self.crossed_image_train_dir_path):
                shutil.rmtree(self.crossed_image_train_dir_path)

            if os.path.exists(self.single_image_train_dir_path):
                shutil.rmtree(self.single_image_train_dir_path)

            if os.path.exists(self.annotations_train_dir_path):
                shutil.rmtree(self.annotations_train_dir_path)

            os.makedirs(self.crossed_image_train_dir_path)
            os.makedirs(self.single_image_train_dir_path)
            os.makedirs(self.annotations_train_dir_path)

            for p in os.listdir(self.annotations_dir_path):
                dataset_name = p.replace('.xml', '')
                crossed_image_path = os.path.join(self.crossed_image_dir_path, dataset_name + '.jpg')
                single_image_path = os.path.join(self.single_image_dir_path, dataset_name + '.jpg')
                crossed_image_train_path = os.path.join(self.crossed_image_train_dir_path, dataset_name + '.jpg')
                single_image_train_path = os.path.join(self.single_image_train_dir_path, dataset_name + '.jpg')

                annotation_path = os.path.join(self.annotations_dir_path, dataset_name + '.xml')
                annotation_train_path = os.path.join(self.annotations_train_dir_path, dataset_name + '.xml')

                crossed_image = Image.open(crossed_image_path)
                single_image = Image.open(single_image_path)
                gt_boxes_position = self.parse_annotation_file(annotation_path)
                crossed_image, single_image, gt_boxes_position = self.transform_data(crossed_image, single_image, gt_boxes_position)

                crossed_image.save(crossed_image_train_path)
                single_image.save(single_image_train_path)

                shutil.copyfile(annotation_path, annotation_train_path)

                doc = Et.parse(annotation_train_path)
                root = doc.getroot()
                for i, obj in enumerate(root.findall('object')):
                    bbox_obj = obj.find('bndbox')
                    bbox_obj.find('xmin').text = str(gt_boxes_position[i][0].numpy())
                    bbox_obj.find('ymin').text = str(gt_boxes_position[i][1].numpy())
                    bbox_obj.find('xmax').text = str(gt_boxes_position[i][2].numpy())
                    bbox_obj.find('ymax').text = str(gt_boxes_position[i][3].numpy())
                doc.write(annotation_train_path)

    def __getitem__(self, index):
        dataset_name = self.dataset_list[index]

        crossed_image_path = os.path.join(self.crossed_image_dir_path, dataset_name + '.jpg')
        single_image_path = os.path.join(self.single_image_dir_path, dataset_name + '.jpg')
        crossed_image_train_path = os.path.join(self.crossed_image_train_dir_path, dataset_name + '.jpg')
        single_image_train_path = os.path.join(self.single_image_train_dir_path, dataset_name + '.jpg')

        annotation_path = os.path.join(self.annotations_dir_path, dataset_name + '.xml')
        annotation_train_path = os.path.join(self.annotations_train_dir_path, dataset_name + '.xml')

        crossed_image = Image.open(crossed_image_train_path)
        single_image = Image.open(single_image_train_path)

        crossed_image = transforms.ToTensor()(crossed_image)
        single_image = transforms.ToTensor()(single_image)

        if torch.cuda.is_available():
            crossed_image, single_image = crossed_image.to(self.device), single_image.to(self.device)

        if not self.train:
            return crossed_image, single_image, \
                   transforms.ToTensor()(Image.open(crossed_image_path)), \
                   transforms.ToTensor()(Image.open(single_image_path)), \
                   self.parse_annotation_file(annotation_path)
        else:
            gt_boxes_position = self.parse_annotation_file(annotation_train_path)  # [x_min, y_min, x_max, y_max]

            s_tensor, m_tensor, l_tensor, s_coords, m_coords, l_coords = self.encode_gt_bboxes(gt_boxes_position, self.iou_thresh)

            if torch.cuda.is_available():
                s_tensor, m_tensor, l_tensor = s_tensor.to(self.device), m_tensor.to(self.device), l_tensor.to(self.device)
                s_coords, m_coords, l_coords = s_coords.to(self.device), m_coords.to(self.device), l_coords.to(self.device)

            return crossed_image, single_image, s_tensor, m_tensor, l_tensor, s_coords, m_coords, l_coords

    def __len__(self):
        return len(self.dataset_list)

    @staticmethod
    def parse_annotation_file(annotation_file):
        bbox_info_list = []
        root = Et.parse(annotation_file).getroot()

        for obj in root.findall('object'):
            bbox_obj = obj.find('bndbox')

            x_min = float(bbox_obj.find('xmin').text)
            y_min = float(bbox_obj.find('ymin').text)
            x_max = float(bbox_obj.find('xmax').text)
            y_max = float(bbox_obj.find('ymax').text)

            bbox_info_list.append([x_min, y_min, x_max, y_max])

        return torch.tensor(bbox_info_list).float()

    def transform_data(self, crossed_image, single_image, gt_boxes_position, is_transform=True, is_save=True, save_path=None):
        w, h = crossed_image.size
        scale = min(self.input_size / w, self.input_size / h)
        resize_w, resize_h = int(w * scale), int(h * scale)
        pad_x, pad_y = (self.input_size - resize_w) // 2, (self.input_size - resize_h) // 2

        transform_image = transforms.Compose([
            transforms.Resize((resize_h, resize_w)),
            transforms.Pad((pad_x, pad_y, pad_x, pad_y), (125, 125, 125)),
        ])

        crossed_image = transform_image(crossed_image)
        single_image = transform_image(single_image)

        # bbox 的尺寸位置也要跟着 image 的变形而变化
        gt_boxes_position[:, [0, 2]] = gt_boxes_position[:, [0, 2]] * scale + pad_x
        gt_boxes_position[:, [1, 3]] = gt_boxes_position[:, [1, 3]] * scale + pad_y

        return crossed_image, single_image, gt_boxes_position

    def encode_gt_bboxes(self, gt_boxes_position, iou_thresh):
        max_boxes_per_scale = 150

        # 3 scale output tensor from Model
        s_tensor = torch.zeros((self.s_output_size, self.s_output_size, self.s_anchors.shape[0], 5))
        m_tensor = torch.zeros((self.m_output_size, self.m_output_size, self.m_anchors.shape[0], 5))
        l_tensor = torch.zeros((self.l_output_size, self.l_output_size, self.l_anchors.shape[0], 5))

        # Save valid vector of保存不同尺度下的生成有效 tensor 中向量的 bbox 尺寸，保持相同的大小是为了多个矩阵成功组成一个 batch，0 为计数器
        s_coords = [torch.zeros((max_boxes_per_scale, 4)), 0]
        m_coords = [torch.zeros((max_boxes_per_scale, 4)), 0]
        l_coords = [torch.zeros((max_boxes_per_scale, 4)), 0]

        # Map a gt box to output tensor
        for position in gt_boxes_position:
            # Convert position to coordination [x, y, w, h]
            coord = torch.cat([(position[2:] + position[:2]) * 0.5, position[2:] - position[:2]], dim=-1)

            # Coordination at different scales of the box
            s_coord, m_coord, l_coord = coord / self.s_scale, coord / self.m_scale, coord / self.l_scale

            # Compute one vector of tensor corresponding to the gt box center
            s_iou, s_tensor, s_coords, s_has_positive = self.compute_vector(s_tensor, s_coords, self.s_anchors, s_coord, coord, iou_thresh)
            m_iou, m_tensor, m_coords, m_has_positive = self.compute_vector(m_tensor, m_coords, self.m_anchors, m_coord, coord, iou_thresh)
            l_iou, l_tensor, l_coords, l_has_positive = self.compute_vector(l_tensor, l_coords, self.l_anchors, l_coord, coord, iou_thresh)

            # If there's no valid vector, taking out the vector of largest IoU
            if not (s_has_positive or m_has_positive or l_has_positive):
                iou_list = torch.cat([s_iou, m_iou, l_iou], dim=-1)
                best_idx = torch.argmax(iou_list, dim=-1)
                best_scale = int(best_idx / self.s_anchors.shape[0])
                best_anchor = int(best_idx % self.s_anchors.shape[0])

                if best_scale == 0:
                    s_tensor, s_coords = self.compute_valid_vectors(s_tensor, s_coords, s_coord, coord, best_anchor)
                elif best_scale == 1:
                    m_tensor, m_coords = self.compute_valid_vectors(m_tensor, m_coords, m_coord, coord, best_anchor)
                else:
                    l_tensor, l_coords = self.compute_valid_vectors(l_tensor, l_coords, l_coord, coord, best_anchor)

        return s_tensor, m_tensor, l_tensor, s_coords[0], m_coords[0], l_coords[0]

    def compute_vector(self, tensor, coords, anchors, scale_coord, raw_coord, iou_thresh):
        anchor_coord = torch.zeros((anchors.shape[0], 4))
        anchor_coord[:, 0:2] = scale_coord[0:2].floor() + 0.5  # Center coordination is center of the cell where center of box locates
        anchor_coord[:, 2:4] = anchors  # The size is anchors' size

        iou = compute_iou(anchor_coord, scale_coord)  # Compute IoU between scaled box and some anchor box
        iou_mask = torch.gt(iou, iou_thresh)  # Is IoU > thresh

        has_positive = False

        if torch.any(iou_mask):
            has_positive = True
            tensor, coords = self.compute_valid_vectors(tensor, coords, scale_coord, raw_coord, iou_mask)

        return iou, tensor, coords, has_positive

    @staticmethod
    def compute_valid_vectors(tensor, coords, scale_coord, raw_coord, mask):
        x_idx, y_idx = torch.floor(scale_coord[0:2]).long()  # Coordination of grid cell

        # Reduce the sensitivity of data
        x_idx, y_idx = torch.abs(x_idx), torch.abs(y_idx)
        if y_idx >= tensor.shape[1]:
            y_idx = tensor.shape[1] - 1
        if x_idx >= tensor.shape[0]:
            x_idx = tensor.shape[0] - 1

        tensor[y_idx, x_idx, mask, :] = 0

        tensor[y_idx, x_idx, mask, 0:4] = raw_coord
        tensor[y_idx, x_idx, mask, 4:5] = 1.0

        coords[0][coords[1], :] = raw_coord
        coords[1] += 1

        return tensor, coords
