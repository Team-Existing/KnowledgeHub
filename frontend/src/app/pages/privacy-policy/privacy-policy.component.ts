import { Component } from '@angular/core'
import { RouterLink } from '@angular/router'
import { IconComponent } from '../../components/icon/icon.component'

@Component({
  selector: 'app-privacy-policy',
  standalone: true,
  imports: [RouterLink, IconComponent],
  templateUrl: './privacy-policy.component.html',
  styleUrl: './privacy-policy.component.css',
})
export class PrivacyPolicyComponent {}
